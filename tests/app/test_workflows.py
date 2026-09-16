"""Deterministic workflow relationship index; no provider calls."""
import json

from app.workflows import build_workflow_index,normalize_identifier,projected_workflow_summary


def row(did,name,**summary):
    return {'document_id':did,'name':name,'summary':json.dumps(summary)}


def test_workflow_identifiers_reject_email_address_suffixes():
    assert normalize_identifier('RFI','42@example.test') is None
    assert normalize_identifier('RFI','42+coord@example.test') is None
    assert normalize_identifier('SUBMITTAL','23-01@example.test') is None
    assert normalize_identifier('RFI','42')=='42'


def test_prefixed_workflow_identifiers_remain_exact_and_distinct():
    assert normalize_identifier('RFI','ARC-0042')=='ARC-0042'
    assert normalize_identifier('SUBMITTAL','MEP-023')=='MEP-023'
    assert normalize_identifier('SUBMITTAL','SUB-001')=='SUB-001'
    assert normalize_identifier('RFI','ARC') is None
    assert normalize_identifier('RFI','ARC--0042') is None
    assert normalize_identifier('RFI','RESPONSE TIME 10 DAYS') is None
    assert normalize_identifier('RFI','ARC-0042@example.test') is None

    result=build_workflow_index([
        row('D1','arc-question.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'ARC-0042','role':'QUESTION','status':None}]),
        row('D2','arc-response.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'arc-0042','role':'RESPONSE','status':None}]),
        row('D3','mep-response.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'MEP-0042','role':'RESPONSE','status':None}]),
    ])
    groups={item['identifier']:item for item in result['items']}
    assert set(groups)=={'ARC-0042','MEP-0042'}
    assert groups['ARC-0042']['state']=='LINKED' and len(groups['ARC-0042']['members'])==2
    assert groups['MEP-0042']['state']=='OPEN' and len(groups['MEP-0042']['members'])==1


def test_conflicting_explicit_rfi_statuses_are_ambiguous_without_precedence():
    result=build_workflow_index([
        row('D1','RFI-42-question.txt',document_type='RFI_QUESTION',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]),
        row('D2','RFI-42-response.txt',document_type='RFI_RESPONSE',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'CLOSED'}]),
    ])

    group=next(item for item in result['items'] if item['kind']=='RFI')
    assert group['state']=='AMBIGUOUS'
    assert {member['status'] for member in group['members']}=={'OPEN','CLOSED'}
    assert any('different explicit statuses' in warning for warning in group['warnings'])


def test_one_rfi_source_with_combined_statuses_is_ambiguous():
    result=build_workflow_index([row(
        'D1','RFI-42.pdf',document_type='RFI_RESPONSE',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'42','role':'MIXED','status':'OPEN / CLOSED'}])])

    group=next(item for item in result['items'] if item['kind']=='RFI')
    assert group['state']=='AMBIGUOUS'
    assert group['members'][0]['status']=='CLOSED / OPEN'
    assert any('One RFI source' in warning for warning in group['warnings'])


def test_projected_summary_preserves_legacy_workflow_fields():
    summary=projected_workflow_summary(json.dumps([
        'RFI_RESPONSE',None,None,None,None,'RFI','0042','RESPONSE',None]))
    result=build_workflow_index([{'document_id':'D1','name':'legacy.pdf','summary':summary}])

    assert result['items'][0]['identifier']=='42'
    assert result['items'][0]['members'][0]['role']=='RESPONSE'


def test_rfi_links_question_response_and_reference_by_normalized_identifier():
    result=build_workflow_index([
        row('D1','question.pdf',document_type='RFI_QUESTION',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'042','role':'QUESTION','status':None}]),
        row('D2','response.pdf',document_type='RFI_RESPONSE',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]),
        row('D3','minutes.txt',document_type='OTHER',workflow_references=[
            {'workflow_type':'RFI','identifier':'00042'}]),
    ])
    item=result['items'][0]
    assert (item['kind'],item['identifier'],item['state'])==('RFI','42','LINKED')
    assert [member['source'] for member in item['members']].count('REFERENCE')==1
    assert result['summary']['rfi_groups']==1 and result['summary']['documents']==3


def test_workflow_index_flags_duplicate_rfi_and_conflicting_submittal_status():
    result=build_workflow_index([
        row('D1','response-a.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'7','role':'RESPONSE','status':None}]),
        row('D2','response-b.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'7','role':'RESPONSE','status':None}]),
        row('D3','submittal-a.pdf',workflow_contexts=[
            {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'APPROVED'}]),
        row('D4','submittal-b.pdf',workflow_contexts=[
            {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'REJECTED'}]),
    ])
    assert [item['state'] for item in result['items']]==['AMBIGUOUS','AMBIGUOUS']
    assert result['summary']['ambiguous']==2


def test_one_submittal_source_with_conflicting_page_statuses_is_ambiguous():
    result=build_workflow_index([row('D1','submittal.pdf',document_type='SUBMITTAL',workflow_contexts=[
        {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
        {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'APPROVED'},
    ])])
    item=result['items'][0]

    assert item['state']=='AMBIGUOUS' and item['members'][0]['status']=='APPROVED / PENDING'
    assert 'One Submittal source' in item['warnings'][0]


def test_reference_without_primary_document_remains_open():
    result=build_workflow_index([row('D1','meeting-minutes.txt',workflow_references=[
        {'workflow_type':'SUBMITTAL','identifier':'23-09-23'}])])
    item=result['items'][0]
    assert item['kind']=='SUBMITTAL' and item['state']=='OPEN'
    assert item['members'][0]['source']=='REFERENCE'
    assert 'primary document' in item['warnings'][0]


def test_submittal_primary_and_exact_reference_are_linked():
    result=build_workflow_index([
        row('D1','submittal.pdf',document_type='SUBMITTAL',workflow_contexts=[
            {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]),
        row('D2','minutes.txt',document_type='OTHER',workflow_references=[
            {'workflow_type':'SUBMITTAL','identifier':'23-01'}]),
    ])
    item=result['items'][0]

    assert item['state']=='LINKED' and not item['warnings']
    assert [member['source'] for member in item['members']]==['REFERENCE','PRIMARY']


def test_roleless_rfi_is_open_and_spaced_submittal_ids_link_exactly():
    result=build_workflow_index([
        row('D1','rfi-cover.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'0008','role':'UNKNOWN','status':None}]),
        row('D2','submittal-a.pdf',workflow_contexts=[
            {'workflow_type':'SUBMITTAL','identifier':'23 05 00 - 01','role':'SUBMITTAL','status':'PENDING'}]),
        row('D3','submittal-b.pdf',workflow_references=[
            {'workflow_type':'SUBMITTAL','identifier':'23 05 00-01'}]),
    ])
    rfi=next(item for item in result['items'] if item['kind']=='RFI')
    submittal=next(item for item in result['items'] if item['kind']=='SUBMITTAL')
    assert rfi['state']=='OPEN' and 'no explicit Question or Response' in rfi['warnings'][0]
    assert submittal['identifier']=='23 05 00-01' and len(submittal['members'])==2


def test_unknown_pages_do_not_manufacture_the_missing_rfi_role():
    result=build_workflow_index([
        row('D1','question.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'1','role':'UNKNOWN','status':None},
            {'workflow_type':'RFI','identifier':'1','role':'QUESTION','status':None}]),
        row('D2','response.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'2','role':'UNKNOWN','status':None},
            {'workflow_type':'RFI','identifier':'2','role':'RESPONSE','status':None}]),
        row('D3','combined.pdf',workflow_contexts=[
            {'workflow_type':'RFI','identifier':'3','role':'UNKNOWN','status':None},
            {'workflow_type':'RFI','identifier':'3','role':'QUESTION','status':None},
            {'workflow_type':'RFI','identifier':'3','role':'RESPONSE','status':None}]),
    ])
    question,response,combined=result['items']

    assert (question['members'][0]['role'],question['state'])==('QUESTION','OPEN')
    assert 'No explicit response' in question['warnings'][0]
    assert (response['members'][0]['role'],response['state'])==('RESPONSE','OPEN')
    assert 'No explicit question' in response['warnings'][0]
    assert (combined['members'][0]['role'],combined['state'])==('MIXED','LINKED')


def test_email_threads_link_only_hashed_exact_headers_and_count_external_references():
    parent='MSG-'+'a'*24;child='MSG-'+'b'*24;missing='MSG-'+'c'*24
    result=build_workflow_index([
        row('D1','z-parent.eml',document_type='EMAIL',email_thread={
            'message_key':parent,'parent_message_key':None,'reference_keys':[]}),
        row('D2','a-reply.eml',document_type='EMAIL',email_thread={
            'message_key':child,'parent_message_key':parent,'reference_keys':[parent,missing]},
            email_content={'quoted_history_chars':42}),
        row('D3','standalone.eml',document_type='EMAIL',email_thread={
            'message_key':None,'parent_message_key':None,'reference_keys':[]}),
    ])
    thread=next(item for item in result['items'] if item['kind']=='EMAIL_THREAD' and len(item['members'])==2)
    standalone=next(item for item in result['items'] if item['kind']=='EMAIL_THREAD' and len(item['members'])==1)
    assert thread['state']=='LINKED' and thread['external_reference_count']==1
    assert [member['document_id'] for member in thread['members']]==['D1','D2']
    assert standalone['state']=='SINGLE'
    assert result['summary']['email_threads']==2 and result['summary']['quoted_email_documents']==1


def test_email_self_reference_is_ambiguous():
    key='MSG-'+'a'*24
    result=build_workflow_index([row('D1','mail.eml',document_type='EMAIL',email_thread={
        'message_key':key,'parent_message_key':key,'reference_keys':[]})])
    item=result['items'][0]

    assert item['state']=='AMBIGUOUS'
    assert 'references its own Message-ID' in item['warnings'][0]


def test_multiple_distinct_message_ids_are_ambiguous_but_missing_id_is_not():
    result=build_workflow_index([
        row('D1','conflicting.eml',document_type='EMAIL',email_thread={
            'message_key':None,'parent_message_key':None,'reference_keys':[],
            'message_id_conflict':True}),
        row('D2','missing.eml',document_type='EMAIL',email_thread={
            'message_key':None,'parent_message_key':None,'reference_keys':[]}),
    ])
    items={item['members'][0]['document_id']:item for item in result['items']}

    assert items['D1']['state']=='AMBIGUOUS'
    assert 'multiple distinct Message-ID' in items['D1']['warnings'][0]
    assert items['D2']['state']=='SINGLE' and not items['D2']['warnings']


def test_email_reference_cycle_is_ambiguous_and_retains_every_member():
    first='MSG-'+'a'*24;second='MSG-'+'b'*24
    result=build_workflow_index([
        row('D1','a.eml',document_type='EMAIL',email_thread={
            'message_key':first,'parent_message_key':second,'reference_keys':[]}),
        row('D2','b.eml',document_type='EMAIL',email_thread={
            'message_key':second,'parent_message_key':first,'reference_keys':[]}),
    ])
    item=result['items'][0]

    assert item['state']=='AMBIGUOUS' and len(item['members'])==2
    assert 'form a cycle' in item['warnings'][0]


def test_selected_email_attachment_relationship_is_deduplicated_without_inheriting_authority():
    rows=[row('MAIL','rfi-42.eml',document_type='EMAIL'),
          row('ATT','response.pdf',document_type='OTHER')]
    link={'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL','document_id':'ATT',
          'attachment_index':0,'content_type':'application/pdf'}
    result=build_workflow_index(rows,[link,link])
    item=next(value for value in result['items'] if value['kind']=='EMAIL_ATTACHMENT')

    assert item['state']=='LINKED' and item['import_count']==2 and item['attachment_index']==0
    assert [(member['role'],member['source'],member['status']) for member in item['members']]==[
        ('MESSAGE','PARENT_EMAIL',None),('ATTACHMENT','SELECTED_ATTACHMENT',None)]
    assert 'not inherited' in item['warnings'][0]
    assert result['summary']['email_attachment_links']==1 and result['summary']['documents']==2
    assert not build_workflow_index(rows,[{**link,'document_id':'MAIL'}])['summary']['email_attachment_links']


def test_attachment_relationship_requires_an_email_parent():
    rows=[row('PARENT','drawing.pdf',document_type='OTHER'),
          row('CHILD','spec.pdf',document_type='OTHER')]
    link={'source_kind':'EMAIL_ATTACHMENT','source_document_id':'PARENT','document_id':'CHILD',
          'attachment_index':0,'content_type':'application/pdf'}

    assert not build_workflow_index(rows,[link])['summary']['email_attachment_links']
