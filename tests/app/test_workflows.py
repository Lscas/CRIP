"""Deterministic workflow relationship index; no provider calls."""
import json

from app.workflows import build_workflow_index


def row(did,name,**summary):
    return {'document_id':did,'name':name,'summary':json.dumps(summary)}


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


def test_reference_without_primary_document_remains_open():
    result=build_workflow_index([row('D1','meeting-minutes.txt',workflow_references=[
        {'workflow_type':'SUBMITTAL','identifier':'23-09-23'}])])
    item=result['items'][0]
    assert item['kind']=='SUBMITTAL' and item['state']=='OPEN'
    assert item['members'][0]['source']=='REFERENCE'
    assert 'primary document' in item['warnings'][0]


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


def test_email_threads_link_only_hashed_exact_headers_and_count_external_references():
    parent='MSG-'+'a'*24;child='MSG-'+'b'*24;missing='MSG-'+'c'*24
    result=build_workflow_index([
        row('D1','question.eml',document_type='EMAIL',email_thread={
            'message_key':parent,'parent_message_key':None,'reference_keys':[]}),
        row('D2','reply.eml',document_type='EMAIL',email_thread={
            'message_key':child,'parent_message_key':parent,'reference_keys':[parent,missing]},
            email_content={'quoted_history_chars':42}),
        row('D3','standalone.eml',document_type='EMAIL',email_thread={
            'message_key':None,'parent_message_key':None,'reference_keys':[]}),
    ])
    thread=next(item for item in result['items'] if item['kind']=='EMAIL_THREAD' and len(item['members'])==2)
    standalone=next(item for item in result['items'] if item['kind']=='EMAIL_THREAD' and len(item['members'])==1)
    assert thread['state']=='LINKED' and thread['external_reference_count']==1
    assert standalone['state']=='SINGLE'
    assert result['summary']['email_threads']==2 and result['summary']['quoted_email_documents']==1


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
