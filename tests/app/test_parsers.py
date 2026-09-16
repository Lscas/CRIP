"""FR-PARSE-001/003/004，基础文字解析，不是施工视觉评测。"""
import io,json,sys,zipfile
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image,ImageDraw
from app.parsers import (MAX_FRAGMENT_BYTES,document_context,parse_file,revision,
                         split_email_history,workflow_references)
from app.parser_worker import save_result
from app.visual_pipeline import (PDF_CROP_COORDINATE_SYSTEM, ocr_pdf_page,
                                 render_pdf_page, render_visual_png)
from app.workflows import build_workflow_index

@pytest.mark.parametrize('text,expected',[
 ('Revision Date: 2026-08-01','2026-08-01'),('Upload Date: 2026-08-01',None),
 ('Revision Date: 2026-02-30',None),('Revision Date: 2026-01-01\nRevision Date: 2026-02-01',None),
 ('修订日期：2026-07-02','2026-07-02'),('2026-08-01',None)])
def test_revision_only_internal(text,expected):assert revision(text)[0]==expected

def test_txt_lines_and_encoding(tmp_path):
    p=tmp_path/'x.txt';p.write_text('Revision Date: 2026-01-01\nline two\nthird',encoding='utf-8')
    r=parse_file(p,p.name);assert r['status']=='SUCCESS'
    f=r['fragments'][0];assert f['locator']['text_line_start']==1 and f['locator']['text_line_end']==3
    p.write_bytes(b'\xff\xfe'+ '中文'.encode('utf-16-le'))
    assert parse_file(p,p.name)['fragments'][0]['text']=='中文'
    p.write_bytes(b'\xffbad');assert parse_file(p,p.name)['status']=='FAILED'

def test_txt_long_no_drop(tmp_path):
    p=tmp_path/'x.txt';text='x'*4000;p.write_text(text, encoding="utf-8")
    r=parse_file(p,p.name);assert ''.join(f['text'] for f in r['fragments'])==text

def test_txt_long_chinese_splits_by_utf8_budget_without_data_loss(tmp_path):
    p=tmp_path/'long-zh.txt';text='施工材料与检查要求。'*156;p.write_text(text,encoding='utf-8')
    fragments=parse_file(p,p.name)['fragments']
    assert len(fragments)>1 and ''.join(f['text'] for f in fragments)==text
    assert all(len(f['text'].encode('utf-8'))<=MAX_FRAGMENT_BYTES for f in fragments)


def test_rfi_txt_separates_question_from_response_evidence(tmp_path):
    path=tmp_path/'RFI-042.txt'
    path.write_text('RFI No: 042\nQuestion:\nMay PVC pipe be used?\nResponse:\nProvide Type L copper pipe.',encoding='utf-8')

    result=parse_file(path,path.name)

    question=next(item for item in result['fragments'] if 'May PVC' in item['text'])
    response=next(item for item in result['fragments'] if 'Type L copper' in item['text'])
    assert result['document_type']=='RFI_RESPONSE'
    assert '> QUESTION' in question['locator']['section']
    assert '> RESPONSE' in response['locator']['section']
    assert question['locator']['text_line_start']<response['locator']['text_line_start']


def test_submittal_txt_retains_explicit_status_without_inferring_approval(tmp_path):
    path=tmp_path/'submittal-221116.txt'
    path.write_text('Submittal No: 22-11-16\nStatus: Pending\nProduct: Type L copper pipe',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['document_type']=='SUBMITTAL'
    assert all('SUBMITTAL 22-11-16 > STATUS: PENDING' in item['locator']['section']
               for item in result['fragments'])


def test_workflow_identifiers_require_digits_and_preserve_uncertain_roles():
    assert workflow_references('The RFI shall be answered before procurement.')==[]
    assert workflow_references('Contact rfi42@example.test or submittal23@example.test.')==[]
    assert workflow_references('Contact rfi42@example.test, then see RFI 77.')==[
        {'workflow_type':'RFI','identifier':'77'}]
    assert workflow_references('See RFI No. 0042 and Submittal 23 05 00-01.')==[
        {'workflow_type':'RFI','identifier':'42'},
        {'workflow_type':'SUBMITTAL','identifier':'23 05 00-01'},
    ]
    assert document_context('RFI 42')=={
        'document_type':'OTHER','workflow_type':'RFI','identifier':'42','role':'UNKNOWN','status':None}


def test_email_addresses_do_not_create_workflow_references(tmp_path):
    message=EmailMessage();message['Subject']='Coordination';message['From']='rfi42@example.test'
    message['To']='submittal23@example.test';message['Cc']='submission24@example.test'
    message.set_content('Contact rfi43@example.test for coordination.\n\n'
                        'On Monday, Pat wrote:\n> From: submittal25@example.test\n> Previous note.')
    path=tmp_path/'address-only.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    text='\n'.join(item['text'] for item in result['fragments'])

    assert result['workflow_contexts']==[] and result['workflow_references']==[]
    assert 'From: rfi42@example.test' in text and 'To: submittal23@example.test' in text


def test_spaced_submittal_identifier_and_common_status_are_canonical():
    context=document_context('Submittal No: 23 05 00 - 01\nStatus: Approved with comments')
    assert context=={'document_type':'SUBMITTAL','workflow_type':'SUBMITTAL',
                     'identifier':'23 05 00-01','role':'SUBMITTAL','status':'APPROVED AS NOTED'}
    filename_context=document_context('', 'Submittal 23 05 00 - 01 Pump Data.pdf')
    assert filename_context['identifier']=='23 05 00-01' and filename_context['status'] is None


def test_forwarded_email_subject_keeps_exact_workflow_identifier():
    context=document_context(
        'Subject: [EXTERNAL] Fwd: Re: RFI No. 0009 - Door hardware\nResponse:\nApproved as noted.')
    assert context['identifier']=='9' and context['role']=='RESPONSE'
    overflow=document_context('Subject: '+'[EXTERNAL] '*7+'RFI 9\nResponse: Approved.')
    assert overflow['workflow_type'] is None


def test_explicit_submittal_subject_wins_over_body_rfi_reference(tmp_path):
    message=EmailMessage();message['Subject']='Re: [External Email] Submittal 23 05 00-01';message.set_content(
        'RFI 42\nStatus: Approved as noted\nResponse package attached separately.')
    path=tmp_path/'RFI-42.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)

    assert (result['workflow_type'],result['document_identifier'],result['workflow_status'])==(
        'SUBMITTAL','23 05 00-01','APPROVED AS NOTED')
    assert result['workflow_contexts'][0]['role']=='SUBMITTAL'
    assert {'workflow_type':'RFI','identifier':'42'} in result['workflow_references']
    rfi=document_context('Subject: RFI 9\nSubmittal 23 05 00-01\nQuestion: Confirm clearance.')
    assert (rfi['workflow_type'],rfi['identifier'],rfi['role'])==('RFI','9','QUESTION')
    body=document_context('Submittal 23 05 00-01\nStatus: Pending','RFI-42.txt')
    assert (body['workflow_type'],body['identifier'])==('SUBMITTAL','23 05 00-01')


def test_email_role_and_status_stay_with_their_exact_body_identifier(tmp_path):
    rfi=EmailMessage();rfi['Subject']='RFI 42';rfi.set_content(
        'RFI 43\nResponse:\nUse Type L copper for RFI 43.')
    rfi_path=tmp_path/'rfi-scope.eml';rfi_path.write_bytes(rfi.as_bytes())
    rfi_result=parse_file(rfi_path,rfi_path.name)

    submittal=EmailMessage();submittal['Subject']='Submittal 23-01';submittal.set_content(
        'Submittal 23-02\nStatus: Rejected\nPump P-2 does not comply.')
    submittal_path=tmp_path/'submittal-scope.eml';submittal_path.write_bytes(submittal.as_bytes())
    submittal_result=parse_file(submittal_path,submittal_path.name)

    assert rfi_result['workflow_contexts'][0]['identifier']=='42'
    assert rfi_result['workflow_contexts'][0]['role']=='UNKNOWN'
    rfi_evidence=next(item for item in rfi_result['fragments'] if 'Type L copper' in item['text'])
    assert 'EMAIL > BODY > RFI 43 > RESPONSE' in rfi_evidence['locator']['section']
    assert {'workflow_type':'RFI','identifier':'43'} in rfi_result['workflow_references']

    assert submittal_result['workflow_contexts'][0]['identifier']=='23-01'
    assert submittal_result['workflow_contexts'][0]['status'] is None
    submittal_evidence=next(item for item in submittal_result['fragments'] if 'Pump P-2' in item['text'])
    assert 'EMAIL > BODY > SUBMITTAL 23-02 > STATUS: REJECTED' in submittal_evidence['locator']['section']
    assert {'workflow_type':'SUBMITTAL','identifier':'23-02'} in submittal_result['workflow_references']


@pytest.mark.parametrize(('subject','body','expected'),[
    ('RFI Status Update','Submittal 23-01\nStatus: Pending\nPump data.',
     ('SUBMITTAL','23-01','SUBMITTAL','PENDING')),
    ('Submittal Coordination','RFI 42\nQuestion:\nConfirm clearance.',
     ('RFI','42','QUESTION',None)),
])
def test_workflow_like_subject_without_identifier_yields_to_exact_body_heading(
        tmp_path,subject,body,expected):
    message=EmailMessage();message['Subject']=subject;message.set_content(body)
    path=tmp_path/'routing.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name);context=result['workflow_contexts'][0]
    visible='\n'.join(item['text'] for item in result['fragments'])

    assert (context['workflow_type'],context['identifier'],context['role'],context['status'])==expected
    assert f'Subject: {subject}' in visible


def test_generic_email_uses_first_explicit_body_workflow_heading(tmp_path):
    message=EmailMessage();message['Subject']='Coordination';message.set_content(
        'Submittal 23-01\nStatus: Pending\nPump P-1 data.\n'
        'RFI 42\nQuestion:\nConfirm clearance.')
    path=tmp_path/'coordination.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    context=result['workflow_contexts'][0]
    pump=next(item for item in result['fragments'] if 'Pump P-1' in item['text'])
    clearance=next(item for item in result['fragments'] if 'Confirm clearance' in item['text'])

    assert (context['workflow_type'],context['identifier'],context['status'])==(
        'SUBMITTAL','23-01','PENDING')
    assert 'EMAIL > BODY > SUBMITTAL 23-01 > STATUS: PENDING' in pump['locator']['section']
    assert 'EMAIL > BODY > RFI 42 > QUESTION' in clearance['locator']['section']
    assert {'workflow_type':'RFI','identifier':'42'} in result['workflow_references']


def test_full_request_for_information_name_is_an_exact_reference(tmp_path):
    message=EmailMessage();message['Subject']='Submittal 23-01';message.set_content(
        'Status: Pending\nSee Request for Information No. 0042 before release.')
    path=tmp_path/'submittal-rfi-reference.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['workflow_type']=='SUBMITTAL'
    assert {'workflow_type':'RFI','identifier':'42'} in result['workflow_references']


def test_submission_alias_is_an_exact_submittal_reference(tmp_path):
    message=EmailMessage();message['Subject']='RFI 42';message.set_content(
        'Response:\nCoordinate with Submission No. 23-01 before release.')
    path=tmp_path/'rfi-submission-reference.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['workflow_type']=='RFI'
    assert {'workflow_type':'SUBMITTAL','identifier':'23-01'} in result['workflow_references']


def test_full_name_workflow_filenames_use_the_existing_fallback():
    rfi=document_context('', 'Request_for_Information_No_0042.pdf')
    submittal=document_context('', 'Submission No. 23-01.pdf')

    assert (rfi['workflow_type'],rfi['identifier'],rfi['role'])==('RFI','42','UNKNOWN')
    assert (submittal['workflow_type'],submittal['identifier'],submittal['status'])==(
        'SUBMITTAL','23-01',None)


def test_prefixed_workflow_identifiers_survive_references_and_filename_fallback():
    assert workflow_references('See RFI No. ARC-0042 and Submittal MEP-023.')==[
        {'workflow_type':'RFI','identifier':'ARC-0042'},
        {'workflow_type':'SUBMITTAL','identifier':'MEP-023'},
    ]
    rfi=document_context('', 'RFI-ARC-0042.pdf')
    submittal=document_context('', 'Submittal-MEP-023.pdf')
    assert (rfi['workflow_type'],rfi['identifier'])==('RFI','ARC-0042')
    assert (submittal['workflow_type'],submittal['identifier'])==('SUBMITTAL','MEP-023')


def test_rfi_role_words_require_an_explicit_heading_boundary(tmp_path):
    full_name=tmp_path/'full-name-rfi.txt';full_name.write_text(
        'Request for Information No. 42\nClarification is pending.',encoding='utf-8')
    timing=tmp_path/'response-time-rfi.txt';timing.write_text(
        'RFI 43\nResponse time: 10 days.\nQuestionnaire attached.',encoding='utf-8')
    metadata=tmp_path/'official-response-metadata.txt';metadata.write_text(
        'RFI 44\nOfficial Response Date: 2026-09-16\nSuggested Answer: Coordinate in field.',
        encoding='utf-8')

    full_name_result=parse_file(full_name,full_name.name)
    timing_result=parse_file(timing,timing.name)
    metadata_result=parse_file(metadata,metadata.name)

    assert full_name_result['workflow_contexts'][0]['role']=='UNKNOWN'
    assert all('RFI 42 > UNKNOWN' in item['locator']['section']
               for item in full_name_result['fragments'])
    assert timing_result['workflow_contexts'][0]['role']=='UNKNOWN'
    assert all('RFI 43 > UNKNOWN' in item['locator']['section']
               for item in timing_result['fragments'])
    assert metadata_result['workflow_contexts'][0]['role']=='UNKNOWN'
    assert all('RFI 44 > UNKNOWN' in item['locator']['section']
               for item in metadata_result['fragments'])


def test_rfi_official_response_heading_is_an_explicit_response(tmp_path):
    path=tmp_path/'RFI-45.txt';path.write_text(
        'RFI 45\nOfficial Response:\nProvide Type L copper pipe.',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['document_type']=='RFI_RESPONSE'
    assert result['workflow_contexts'][0]['role']=='RESPONSE'
    assert any('RFI 45 > RESPONSE' in item['locator']['section']
               for item in result['fragments'] if 'Provide Type L' in item['text'])


@pytest.mark.parametrize(('source_status','expected_status'),[
    ('Draft','DRAFT'),
    ('Open In Review','OPEN IN REVIEW'),
    ('Closed - Revised','CLOSED-REVISED'),
    ('Voided','VOID'),
])
def test_exact_rfi_status_is_preserved_without_manufacturing_a_role(
        tmp_path,source_status,expected_status):
    path=tmp_path/'RFI-46.txt';path.write_text(
        f'RFI 46\nRFI Status: {source_status}\nCoordination record.',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['status']==expected_status
    assert result['workflow_contexts'][0]['role']=='UNKNOWN'
    assert any(f'RFI 46 > UNKNOWN > STATUS: {expected_status}' in item['locator']['section']
               for item in result['fragments'])


def test_rfi_status_metadata_and_status_prefixed_prose_remain_neutral(tmp_path):
    path=tmp_path/'RFI-47.txt';path.write_text(
        'RFI 47\nStatus Date: 2026-09-16\nRFI Status Update: Closed\n'
        'Status: Open items remain for coordination.',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['status'] is None
    assert all('STATUS:' not in item['locator']['section'] for item in result['fragments'])
    assert document_context('RFI Status: Closed')['workflow_type'] is None


def test_roleless_rfi_stays_unknown_and_not_implicitly_a_question(tmp_path):
    path=tmp_path/'RFI-42.txt';path.write_text('RFI 42\nClarification is pending.',encoding='utf-8')
    result=parse_file(path,path.name)
    assert result['document_type']=='OTHER'
    assert all('RFI 42 > UNKNOWN' in item['locator']['section'] for item in result['fragments'])


def test_not_approved_submittal_status_is_canonical_rejected(tmp_path):
    path=tmp_path/'submittal.txt'
    path.write_text('Submittal No: 23 05 00-02\nStatus: Not Approved\nPump P-1',encoding='utf-8')
    result=parse_file(path,path.name)
    assert result['workflow_contexts'][0]['status']=='REJECTED'
    assert all('STATUS: REJECTED' in item['locator']['section'] for item in result['fragments'])


@pytest.mark.parametrize(('label','source_status','expected_status'),[
    ('Status','Furnish as Submitted','APPROVED'),
    ('Status','Furnish as Corrected','APPROVED AS NOTED'),
    ('Status','Amend and Resubmit','REVISE AND RESUBMIT'),
    ('Review Response','Approved as Noted','APPROVED AS NOTED'),
    ('Final Response','Revise and Resubmit','REVISE AND RESUBMIT'),
    ('Submittal Response','No Exceptions Taken','APPROVED'),
])
def test_common_submittal_return_statuses_use_existing_canonical_groups(
        tmp_path,label,source_status,expected_status):
    path=tmp_path/'submittal.txt'
    path.write_text(
        f'Submittal No: 23 05 00-03\n{label}: {source_status}\nPump P-2',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['status']==expected_status
    assert all(f'STATUS: {expected_status}' in item['locator']['section']
               for item in result['fragments'])


def test_submittal_response_metadata_is_not_a_disposition(tmp_path):
    path=tmp_path/'submittal.txt';path.write_text(
        'Submittal 23-04\nReview Response Time: 5 days\nFinal Response Due: 2026-09-30',
        encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['status'] is None
    assert all('STATUS:' not in item['locator']['section'] for item in result['fragments'])


def test_one_text_submittal_preserves_conflicting_explicit_statuses(tmp_path):
    path=tmp_path/'Submittal-23-01.txt';path.write_text(
        'Submittal 23-01\nStatus: Pending\nPump data received.\n'
        'Status: Rejected\nWrong pump selected.',encoding='utf-8')

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['status']=='PENDING / REJECTED'
    sections=[item['locator']['section'] for item in result['fragments']]
    assert any('STATUS: PENDING' in section for section in sections)
    assert any('STATUS: REJECTED' in section for section in sections)


def test_eml_parses_safe_body_and_inventories_attachment_without_analyzing_it(tmp_path):
    message=EmailMessage();message['Subject']='RFI 042';message['From']='contractor@example.test'
    message['To']='engineer@example.test';message.set_content(
        'Response:\nProvide Type L copper pipe.\nRevision Date: 2026-09-15')
    message.add_attachment(b'ATTACHMENT-ONLY SECRET REQUIREMENT',maintype='application',subtype='pdf',
                           filename='response.pdf')
    path=tmp_path/'message.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    text='\n'.join(item['text'] for item in result['fragments'])

    assert result['document_type']=='EMAIL' and result['workflow_type']=='RFI'
    assert result['active_content_processed'] is False
    assert result['attachments']==[{'file_name':'response.pdf','content_type':'application/pdf',
                                    'status':'NOT_PROCESSED'}]
    assert 'Type L copper pipe' in text and 'ATTACHMENT-ONLY' not in text
    body=next(item for item in result['fragments'] if item['locator']['native_element_id']=='email-body')
    assert 'EMAIL > BODY > RFI 42 > RESPONSE' in body['locator']['section']
    assert body['internal_revision_date']=='2026-09-15'
    assert any('not analyzed' in warning for warning in result['warnings'])


def test_msg_reuses_safe_email_evidence_without_reading_attachment_content(tmp_path,monkeypatch):
    attachment=SimpleNamespace(file_name='../response.pdf',mime_type='application/pdf',
                               file_bytes=b'ATTACHMENT-ONLY SECRET REQUIREMENT')
    message=SimpleNamespace(
        message_headers={'Message-ID':'<reply@example.test>',
                         'In-Reply-To':'<question@example.test>'},
        subject='RFI 042',sender='contractor@example.test',recipients=(),sent_date=None,
        body='Response:\nProvide Type L copper pipe.',html_body=None,attachments=(attachment,))
    class Message:
        @classmethod
        def load(cls,path):
            assert Path(path).name=='response.msg'
            return message
    monkeypatch.setitem(sys.modules,'oxmsg',SimpleNamespace(Message=Message))
    path=tmp_path/'response.msg';path.write_bytes(b'synthetic-msg-container')

    result=parse_file(path,path.name);text='\n'.join(item['text'] for item in result['fragments'])

    assert result['document_type']=='EMAIL' and result['workflow_type']=='RFI'
    assert result['attachments']==[{'file_name':'response.pdf','content_type':'application/pdf',
                                    'status':'NOT_PROCESSED'}]
    assert 'Type L copper pipe' in text and 'ATTACHMENT-ONLY' not in text
    assert result['email_thread']['message_key'].startswith('MSG-')
    assert 'reply@example.test' not in str(result) and result['active_content_processed'] is False


def test_eml_attached_message_and_its_nested_files_stay_out_of_parent_evidence(tmp_path):
    nested=EmailMessage();nested['Subject']='RFI 901';nested.set_content(
        'Question:\nATTACHMENT-ONLY: May PVC be used?')
    nested.add_attachment(b'INNER-ONLY',maintype='application',subtype='pdf',filename='inner.pdf')
    outer=EmailMessage();outer['Subject']='RFI 901';outer.set_content('Response:\nUse Type L copper.')
    outer.add_attachment(nested,filename='forwarded.eml')
    path=tmp_path/'outer.eml';path.write_bytes(outer.as_bytes())

    result=parse_file(path,path.name)
    text='\n'.join(item['text'] for item in result['fragments'])

    assert 'Use Type L copper' in text and 'ATTACHMENT-ONLY' not in text and 'INNER-ONLY' not in text
    assert result['workflow_contexts'][0]['role']=='RESPONSE'
    assert result['attachments']==[{'file_name':'forwarded.eml','content_type':'message/rfc822',
                                    'status':'NOT_PROCESSED'}]


def test_eml_html_removes_active_content_and_never_fetches_remote_resources(tmp_path):
    message=EmailMessage();message['Subject']='Submittal 23-09-23';message.set_content(
        '<html><style>.hidden{display:none}</style><body><p>Status: Reviewed</p>'
        '<p>Air handling unit AHU-1</p><script>ACTIVE-CONTENT</script>'
        '<img src="https://invalid.example.test/tracker.png"></body></html>',subtype='html')
    path=tmp_path/'html.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    text='\n'.join(item['text'] for item in result['fragments'])

    assert 'Air handling unit AHU-1' in text
    assert 'ACTIVE-CONTENT' not in text and 'tracker.png' not in text
    assert result['workflow_type']=='SUBMITTAL' and result['active_content_processed'] is False


def test_eml_blank_plain_alternative_falls_back_to_html_but_nonempty_plain_remains_preferred(tmp_path):
    blank=EmailMessage();blank['Subject']='Project update';blank.set_content('   \n')
    blank.add_alternative(
        '<html><body><p>RFI 205</p><p>Response:</p><p>Provide Type L copper.</p></body></html>',
        subtype='html')
    blank_path=tmp_path/'blank-plain.eml';blank_path.write_bytes(blank.as_bytes())
    fallback=parse_file(blank_path,blank_path.name)

    preferred=EmailMessage();preferred['Subject']='Coordination'
    preferred.set_content('Plain current requirement.')
    preferred.add_alternative('<html><body><p>HTML-ONLY DIFFERENT TEXT</p></body></html>',subtype='html')
    preferred_path=tmp_path/'preferred-plain.eml';preferred_path.write_bytes(preferred.as_bytes())
    plain=parse_file(preferred_path,preferred_path.name)

    fallback_text='\n'.join(item['text'] for item in fallback['fragments'])
    plain_text='\n'.join(item['text'] for item in plain['fragments'])
    assert 'Type L copper' in fallback_text and fallback['workflow_contexts'][0]['role']=='RESPONSE'
    assert fallback['email_content']['current_body_chars']>0
    assert 'Plain current requirement.' in plain_text and 'HTML-ONLY' not in plain_text
    assert all('multiple non-empty' not in warning for warning in plain['warnings'])


def test_eml_same_type_alternatives_select_one_body_instead_of_merging_roles(tmp_path):
    message=EmailMessage();message['Subject']='RFI 301';message.make_alternative()
    message.add_alternative('Question:\nConfirm clearance.',subtype='plain')
    message.add_alternative('Official Response:\nUse 4 inches.',subtype='plain')
    path=tmp_path/'conflicting-alternatives.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    body='\n'.join(item['text'] for item in result['fragments']
                   if item['locator']['native_element_id']=='email-body')

    assert result['status']=='PARTIAL'
    assert result['workflow_contexts'][0]['role']=='QUESTION'
    assert 'Confirm clearance.' in body and 'Use 4 inches.' not in body
    assert any('multiple non-empty text/plain alternatives' in warning
               for warning in result['warnings'])


def test_eml_separates_quoted_history_and_hashes_thread_headers(tmp_path):
    message=EmailMessage();message['Subject']='RFI 009';message['Message-ID']='<reply@example.test>'
    message['In-Reply-To']='<question@example.test>';message['References']='<question@example.test>'
    message.set_content('Response:\nUse Type L copper.\n\nOn Monday, Pat wrote:\n'
                        '> Revision Date: 2025-01-01\n> Question: May PVC be used?')
    path=tmp_path/'reply.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name);serialized=str(result)
    current=next(item for item in result['fragments'] if item['locator']['native_element_id']=='email-body')
    quoted=next(item for item in result['fragments'] if item['locator']['native_element_id']=='email-quoted-history')

    assert 'Type L copper' in current['text'] and current['internal_revision_date'] is None
    assert 'Revision Date' in quoted['text'] and quoted['internal_revision_date']=='2025-01-01'
    assert 'EMAIL > QUOTED HISTORY' in quoted['locator']['section']
    assert result['email_content']['quoted_history_chars']>0
    assert result['email_thread']['message_key'].startswith('MSG-')
    assert result['email_thread']['parent_message_key'] in result['email_thread']['reference_keys']
    assert 'reply@example.test' not in serialized and 'question@example.test' not in serialized


def test_eml_flags_multiple_distinct_message_ids_without_persisting_raw_values(tmp_path):
    raw=(b'Subject: Coordination\r\n'
         b'Message-ID: <first@example.test>\r\n'
         b'Message-ID: <second@example.test>\r\n'
         b'Content-Type: text/plain; charset=utf-8\r\n\r\nCurrent coordination text.')
    path=tmp_path/'conflicting-message-id.eml';path.write_bytes(raw)

    result=parse_file(path,path.name);serialized=json.dumps(result)

    assert result['email_thread']['message_key'] is None
    assert result['email_thread']['message_id_conflict'] is True
    assert 'first@example.test' not in serialized and 'second@example.test' not in serialized


def test_eml_malformed_thread_tokens_cannot_link_unrelated_messages(tmp_path):
    def parse(name,message_id,parent=None,references=None):
        message=EmailMessage();message['Subject']='Coordination';message['Message-ID']=message_id
        if parent:message['In-Reply-To']=parent
        if references:message['References']=references
        message.set_content('Current coordination text.')
        path=tmp_path/name;path.write_bytes(message.as_bytes())
        return parse_file(path,path.name)

    first=parse('one.eml','unavailable')
    second=parse('two.eml','<second@example.test>','unavailable','<also-invalid>')
    rows=[{'document_id':did,'name':name,'summary':json.dumps(result)} for did,name,result in
          [('D1','one.eml',first),('D2','two.eml',second)]]
    threads=[item for item in build_workflow_index(rows)['items'] if item['kind']=='EMAIL_THREAD']

    assert first['email_thread']['message_key'] is None
    assert second['email_thread']['parent_message_key'] is None
    assert second['email_thread']['reference_keys']==[]
    assert len(threads)==2 and all(item['state']=='SINGLE' and len(item['members'])==1
                                   for item in threads)


def test_eml_outlook_inline_header_starts_quoted_history_without_mixing_rfi_role(tmp_path):
    message=EmailMessage();message['Subject']='RFI 088';message.set_content(
        '<html><body><p>Response:</p><p>Use Type L copper.</p>'
        '<p>From: Contractor &lt;c@example.test&gt; Sent: Monday To: Engineer Subject: RFI 088</p>'
        '<p>Question: May PVC be used?</p></body></html>',subtype='html')
    path=tmp_path/'outlook-reply.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    current=next(item for item in result['fragments'] if item['locator']['native_element_id']=='email-body')
    quoted=next(item for item in result['fragments']
                if item['locator']['native_element_id']=='email-quoted-history')

    assert 'Type L copper' in current['text'] and 'May PVC' not in current['text']
    assert 'From: Contractor' in quoted['text'] and 'May PVC be used?' in quoted['text']
    assert result['workflow_contexts'][0]['role']=='RESPONSE'
    assert result['email_content']['quoted_history_chars']>0
    ordinary,history=split_email_history(
        'From: the site team\nThe note mentions subject: coordination but is current text.')
    assert 'subject: coordination' in ordinary and history==''


def test_eml_multiple_in_reply_to_ids_link_one_exact_hashed_thread(tmp_path):
    def parse(name,message_id,in_reply_to=None):
        message=EmailMessage();message['Subject']='Coordination note';message['Message-ID']=message_id
        if in_reply_to:message['In-Reply-To']=in_reply_to
        message.set_content('Current coordination text.')
        path=tmp_path/name;path.write_bytes(message.as_bytes())
        return parse_file(path,path.name)

    root=parse('root.eml','<root@example.test>')
    branch=parse('branch.eml','<branch@example.test>')
    child=parse('child.eml','<child@example.test>',
                '<root@example.test> <branch@example.test>')
    thread=child['email_thread']

    assert thread['parent_message_key']==branch['email_thread']['message_key']
    assert thread['reference_keys']==[root['email_thread']['message_key']]
    assert 'example.test' not in str(thread)
    rows=[{'document_id':name,'name':name+'.eml','summary':json.dumps(result)}
          for name,result in [('D1',root),('D2',branch),('D3',child)]]
    index=build_workflow_index(rows)
    threads=[item for item in index['items'] if item['kind']=='EMAIL_THREAD']
    assert len(threads)==1 and threads[0]['state']=='LINKED' and len(threads[0]['members'])==3
    assert [member['document_id'] for member in threads[0]['members']]==['D1','D2','D3']


def test_eml_html_blockquote_is_history_not_current_body(tmp_path):
    message=EmailMessage();message['Subject']='Submittal 23-09-23';message.set_content(
        '<html><body><p>Status: Pending</p><p>Current note.</p>'
        '<blockquote><p>On Monday, Pat wrote:</p><p>Status: Approved</p><p>Old note.</p></blockquote>'
        '<p>Current after quote.</p></body></html>',subtype='html')
    path=tmp_path/'thread.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    current='\n'.join(item['text'] for item in result['fragments']
                      if item['locator']['native_element_id']=='email-body')
    quoted='\n'.join(item['text'] for item in result['fragments']
                     if item['locator']['native_element_id']=='email-quoted-history')

    assert 'Current note.' in current and 'Current after quote.' in current and 'Approved' not in current
    assert 'Approved' in quoted and 'Old note.' in quoted and 'Current after quote.' not in quoted
    assert result['workflow_status']=='PENDING'


@pytest.mark.parametrize('wrapper',['class="gmail_quote gmail_quote_container"',
                                    'id="divRplyFwdMsg"'])
def test_eml_common_html_reply_wrappers_are_history_and_current_text_resumes(tmp_path,wrapper):
    message=EmailMessage();message['Subject']='Submittal 23-09-23';message.set_content(
        '<html><body><p>Status: Pending</p><p>Current note.</p>'
        f'<div {wrapper}><p>From: Reviewer</p><div><p>Status: Approved</p>'
        '<p>Old note.</p></div></div>'
        '<p>Current after quote.</p></body></html>',subtype='html')
    path=tmp_path/'wrapped-thread.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    current='\n'.join(item['text'] for item in result['fragments']
                      if item['locator']['native_element_id']=='email-body')
    quoted='\n'.join(item['text'] for item in result['fragments']
                     if item['locator']['native_element_id']=='email-quoted-history')

    assert 'Current note.' in current and 'Current after quote.' in current
    assert 'Approved' not in current and 'Old note.' not in current
    assert 'Approved' in quoted and 'Old note.' in quoted and 'Current after quote.' not in quoted
    assert result['workflow_status']=='PENDING'


def test_eml_plain_signature_delimiter_is_separate_from_current_rfi_body(tmp_path):
    message=EmailMessage();message['Subject']='RFI 42';message.set_content(
        'Response:\nProvide Type L copper pipe.\n-- \nPat Smith\nAcme Equipment Group\nRFI 999 Desk')
    path=tmp_path/'plain-signature.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    current='\n'.join(item['text'] for item in result['fragments']
                      if item['locator']['native_element_id']=='email-body')
    signature='\n'.join(item['text'] for item in result['fragments']
                        if item['locator']['native_element_id']=='email-signature')

    assert 'Type L copper pipe' in current and 'Acme Equipment Group' not in current
    assert 'Pat Smith' in signature and 'Acme Equipment Group' in signature
    assert result['workflow_contexts'][0]['role']=='RESPONSE'
    assert {'workflow_type':'RFI','identifier':'999'} not in result['workflow_references']
    assert result['email_content']['signature_chars']>0


def test_eml_gmail_signature_wrapper_is_separate_and_current_text_resumes(tmp_path):
    message=EmailMessage();message['Subject']='Submittal 23-09-23';message.set_content(
        '<html><body><p>Status: Pending</p><p>Current before.</p>'
        '<div class="gmail_signature"><div>Pat Smith</div><div>Acme Equipment Group</div></div>'
        '<p>Current after.</p></body></html>',subtype='html')
    path=tmp_path/'gmail-signature.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    current='\n'.join(item['text'] for item in result['fragments']
                      if item['locator']['native_element_id']=='email-body')
    signature='\n'.join(item['text'] for item in result['fragments']
                        if item['locator']['native_element_id']=='email-signature')

    assert 'Current before.' in current and 'Current after.' in current
    assert 'Acme Equipment Group' not in current and 'Acme Equipment Group' in signature
    assert result['workflow_status']=='PENDING' and result['email_content']['signature_chars']>0


def test_eml_quoted_signature_cannot_create_a_workflow_reference(tmp_path):
    message=EmailMessage();message['Subject']='RFI 42';message.set_content(
        'Response:\nProvide Type L copper.\n\nOn Monday, Pat wrote:\n'
        '> Question: May PVC be used?\n> -- \n> Pat Smith\n> RFI 999 Desk')
    path=tmp_path/'quoted-signature.eml';path.write_bytes(message.as_bytes())

    result=parse_file(path,path.name)
    signature='\n'.join(item['text'] for item in result['fragments']
                        if item['locator']['native_element_id']=='email-quoted-signature')
    quoted='\n'.join(item['text'] for item in result['fragments']
                     if item['locator']['native_element_id']=='email-quoted-history')

    assert 'May PVC be used?' in quoted and 'RFI 999 Desk' not in quoted
    assert 'RFI 999 Desk' in signature
    assert {'workflow_type':'RFI','identifier':'999'} not in result['workflow_references']

def test_docx_minimal(tmp_path):
    p=tmp_path/'x.docx'
    xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Material alpha</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Quantity 2</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',xml)
    r=parse_file(p,p.name);assert len(r['fragments'])==2 and r['status']=='PARTIAL'
    assert r['fragments'][1]['locator']['native_element_id']=='body-element-2'

def test_docx_entity_rejected(tmp_path):
    p=tmp_path/'x.docx'
    with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>')
    with pytest.raises(Exception):parse_file(p,p.name)

def test_image_pending(tmp_path):
    p=tmp_path/'a.png';Image.new('RGB',(10,10)).save(p)
    r=parse_file(p,p.name);assert r['status']=='PARTIAL' and not r['fragments']


def test_local_ocr_returns_traceable_boxes_and_confidence(tmp_path):
    p=tmp_path/'ocr.png';image=Image.new('RGB',(900,180),'white')
    ImageDraw.Draw(image).text((30,55),'MATERIAL DOOR D-101',fill='black',stroke_width=1)
    image.save(p)
    r=parse_file(p,p.name)
    assert len(r['visual_tasks'])==1
    task=r['visual_tasks'][0]
    assert (task['reason'],task['region_type'],task['bbox'],task['page_type'])==(
        'IMAGE_INPUT','FULL_PAGE',None,'GRAPHIC_OR_SCAN')
    assert r['fragments'] and all(f['method']=='OCR' for f in r['fragments'])
    assert all(0<=f['confidence']<=1 for f in r['fragments'])
    assert all(f['locator']['coordinate_system']=='image-pixels-top-left-exif-normalized' for f in r['fragments'])
    assert all(len(f['locator']['bbox'])==4 and f['text_map'] for f in r['fragments'])

def test_image_render_applies_exif_orientation_and_names_coordinate_system(tmp_path):
    path=tmp_path/'oriented.jpg';image=Image.new('RGB',(80,40),'white')
    exif=Image.Exif();exif[274]=6;image.save(path,exif=exif)
    raw,width,height,coordinate_system=render_visual_png(path,path.name)
    with Image.open(io.BytesIO(raw)) as rendered:
        assert rendered.size==(40,80)
    assert (width,height)==(40.0,80.0)
    assert coordinate_system=='image-pixels-top-left-exif-normalized'

def test_pdf_text_and_bbox(tmp_path):
    from reportlab.pdfgen import canvas
    p=tmp_path/'a.pdf';c=canvas.Canvas(str(p));c.drawString(72,700,'Provide test materials.');c.save()
    progress=[];r=parse_file(p,p.name,progress=progress.append)
    assert progress and progress[-1]['fragments'][0]['text']=='Provide test materials.'
    assert progress[-1]['page_count']==1
    assert r['fragments'][0]['locator']['page_number']==1
    assert len(r['fragments'][0]['locator']['bbox'])==4
    assert 'Provide' in r['fragments'][0]['text']


def test_rfi_pdf_filename_context_still_respects_explicit_response_heading(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'RFI-109.pdf';drawing=canvas.Canvas(str(path))
    drawing.drawString(72,700,'Response:');drawing.drawString(72,675,'Provide Type L copper pipe.');drawing.save()

    result=parse_file(path,path.name)

    assert result['document_type']=='RFI_RESPONSE'
    assert all('RFI 109 > RESPONSE' in (item['locator']['section'] or '') for item in result['fragments'])


def test_rfi_pdf_filename_context_preserves_exact_status_without_role_inference(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'RFI-110.pdf';drawing=canvas.Canvas(str(path))
    drawing.drawString(72,700,'RFI Status: Closed')
    drawing.drawString(72,675,'Coordination record.');drawing.save()

    result=parse_file(path,path.name)

    assert result['workflow_contexts'][0]['role']=='UNKNOWN'
    assert result['workflow_contexts'][0]['status']=='CLOSED'
    assert all('RFI 110 > UNKNOWN > STATUS: CLOSED' in (item['locator']['section'] or '')
               for item in result['fragments'])


def test_single_pdf_page_workers_preserve_order_and_content(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'parallel.pdf';drawing=canvas.Canvas(str(path))
    for page in range(1,9):
        drawing.drawString(72,700,f'Page {page} copper pipe requirement')
        drawing.showPage()
    drawing.save()

    serial=parse_file(path,path.name,workers=1)
    parallel=parse_file(path,path.name,workers=2)

    assert parallel['pages']==serial['pages']
    assert [item['text'] for item in parallel['fragments']]==[item['text'] for item in serial['fragments']]
    assert [item['locator']['page_number'] for item in parallel['fragments']]==list(range(1,9))


def test_rich_text_page_with_decorative_rule_skips_vision_but_large_sheet_keeps_it(tmp_path):
    from reportlab.pdfgen import canvas
    text='Provide materials and execute inspections in accordance with the project specifications.'
    ordinary=tmp_path/'ordinary.pdf';drawing=canvas.Canvas(str(ordinary))
    drawing.drawString(72,700,text);drawing.line(72,680,500,680);drawing.save()
    assert parse_file(ordinary,ordinary.name)['visual_tasks']==[]
    large=tmp_path/'large.pdf';drawing=canvas.Canvas(str(large),pagesize=(1600,1000))
    drawing.drawString(72,900,text);drawing.line(72,880,1500,880);drawing.save()
    tasks=parse_file(large,large.name)['visual_tasks']
    assert len(tasks)==1 and tasks[0]['reason']=='LARGE_FORMAT'
    assert tasks[0]['region_type']=='FULL_PAGE' and tasks[0]['page_type']=='DRAWING'


def test_pdf_cropbox_is_the_only_rendered_and_analyzed_region(tmp_path,monkeypatch):
    """All page-derived locators are local to the same visible CropBox PNG."""
    from reportlab.pdfgen import canvas
    path=tmp_path/'cropped.pdf';drawing=canvas.Canvas(str(path),pagesize=(600,800))
    # The non-zero crop origin deliberately excludes the HIDDEN label.
    drawing.setCropBox([100,100,500,600])
    drawing.drawString(150,750,'HIDDEN OUTSIDE CROPBOX')
    drawing.drawString(150,400,'VISIBLE INSIDE CROPBOX')
    drawing.save()

    rendered,width,height=render_pdf_page(path,1,resolution=72)
    assert (width,height)==(400.0,500.0) and rendered.size==(400,500)
    raw,vwidth,vheight,coordinate_system=render_visual_png(path,path.name,1)
    assert (vwidth,vheight,coordinate_system)==(400.0,500.0,PDF_CROP_COORDINATE_SYSTEM)
    with Image.open(io.BytesIO(raw)) as preview:
        assert preview.width / preview.height == pytest.approx(400 / 500,rel=0.01)
    cropped,cwidth,cheight,crop_system=render_visual_png(path,path.name,1,[50,50,250,300])
    assert (cwidth,cheight,crop_system)==(400.0,500.0,PDF_CROP_COORDINATE_SYSTEM)
    with Image.open(io.BytesIO(cropped)) as preview:
        assert preview.width / preview.height == pytest.approx(200 / 250,rel=0.01)

    result=parse_file(path,path.name)
    text='\n'.join(fragment['text'] for fragment in result['fragments'])
    assert 'VISIBLE INSIDE CROPBOX' in text and 'HIDDEN OUTSIDE CROPBOX' not in text
    fragment=result['fragments'][0]
    assert fragment['locator']['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM
    assert all(0 <= value <= limit for value,limit in zip(fragment['locator']['bbox'],[400,500,400,500]))
    assert all(0 <= value <= limit for value,limit in zip(fragment['text_map'][0]['bbox'],[400,500,400,500]))

    class Engine:
        def __call__(self,array):
            height_px,width_px=array.shape[:2]
            return SimpleNamespace(txts=['OCR VISIBLE'],scores=[0.9],boxes=[[
                [0,0],[width_px / 2,0],[width_px / 2,height_px / 2],[0,height_px / 2]
            ]])
    monkeypatch.setattr('app.visual_pipeline.local_ocr_available',lambda:True)
    monkeypatch.setattr('app.visual_pipeline._ocr_engine',lambda:Engine())
    ocr=ocr_pdf_page(path,1)
    assert ocr[0]['locator']['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM
    assert ocr[0]['locator']['bbox']==pytest.approx([0,0,200,250],abs=1.0)


def test_low_density_pdf_text_layer_also_runs_ocr_fallback(tmp_path,monkeypatch):
    from reportlab.pdfgen import canvas
    path=tmp_path/'sparse-scan.pdf';drawing=canvas.Canvas(str(path))
    drawing.drawString(72,700,'X');drawing.line(72,650,144,650);drawing.save()
    fake=[{'text':'VISIBLE SCANNED NOTE','locator':{'page_number':1,'sheet':None,'section':None,
           'paragraph':None,'bbox':[10,20,200,40],'coordinate_system':'pdf-points-top-left',
           'text_line_start':None,'text_line_end':None,'native_element_id':None},
           'method':'OCR','internal_revision_date':None,'revision_label':None,
           'text_map':[{'start':0,'end':20,'bbox':[10,20,200,40]}],'confidence':0.91}]
    monkeypatch.setattr('app.parsers.local_ocr_available',lambda:True)
    monkeypatch.setattr('app.parsers.ocr_pdf_page',lambda source,page:fake)
    result=parse_file(path,path.name)
    assert {fragment['method'] for fragment in result['fragments']}=={'TEXT_LAYER','OCR'}
    assert result['pages'][0]['status']=='TEXT_AND_OCR_VISUAL_PENDING'
    assert any('文字层密度低' in warning for warning in result['warnings'])


def test_pdf_vector_geometry_requires_explicit_scale_and_does_not_claim_material_quantity(tmp_path):
    from reportlab.pdfgen import canvas
    p=tmp_path/'scaled.pdf';c=canvas.Canvas(str(p));c.drawString(72,700,'SCALE: 1/4" = 1\'-0"')
    c.line(72,650,144,650);c.save()
    r=parse_file(p,p.name);g=r['geometry_summaries'][0]
    assert g['calibration']['ratio']==48 and g['status']=='CALIBRATED_RAW_GEOMETRY'
    assert g['calibrated_total_length']['unit']=='FT'
    assert g['material_quantity'] is None and '不作为设计净量' in g['scope_note']


def test_pdf_spec_structure_and_schedule_rows_keep_relationships_and_route_one_crop(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'schedule.pdf';drawing=canvas.Canvas(str(path),pagesize=(1600,1000))
    drawing.drawString(72,940,'SECTION 22 11 16')
    drawing.drawString(72,910,'PART 2 - PRODUCTS')
    drawing.drawString(72,880,'2.1 EQUIPMENT SCHEDULE')
    xs=[72,360,620,820];ys=[820,780,740]
    for x in xs:drawing.line(x,ys[-1],x,ys[0])
    for y in ys:drawing.line(xs[0],y,xs[-1],y)
    drawing.drawString(82,792,'Equipment');drawing.drawString(370,792,'Size');drawing.drawString(630,792,'Quantity')
    drawing.drawString(82,752,'Backflow preventer');drawing.drawString(370,752,'2 inch');drawing.drawString(630,752,'2')
    drawing.save()

    result=parse_file(path,path.name)

    assert result['pages'][0]['page_type']=='SCHEDULE'
    assert result['pages'][0]['table_count']>=1
    rows=[item for item in result['fragments'] if (item['locator']['paragraph'] or '').startswith('Table ')]
    assert rows and any('Backflow preventer' in item['text'] and '2 inch' in item['text'] for item in rows)
    assert all('22 11 16' in (item['locator']['section'] or '') for item in rows)
    assert len(result['visual_tasks'])==1
    task=result['visual_tasks'][0]
    assert task['region_type']=='TABLE' and len(task['bbox'])==4
    assert task['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM


def test_dxf_object_count_and_known_unit_geometry_are_pending_review(tmp_path):
    import ezdxf
    p=tmp_path/'drawing.dxf';doc=ezdxf.new();doc.units=6
    doc.layers.add('PIPE');msp=doc.modelspace();msp.add_line((0,0),(3,4),dxfattribs={'layer':'PIPE'})
    block=doc.blocks.new('VALVE');block.add_circle((0,0),1)
    msp.add_blockref('VALVE',(1,1),dxfattribs={'layer':'PIPE'});msp.add_blockref('VALVE',(2,2),dxfattribs={'layer':'PIPE'})
    doc.saveas(p)
    r=parse_file(p,p.name)
    assert r['cad_level']=='OBJECT_METADATA' and r['status']=='SUCCESS'
    assert any(f['method']=='CAD_OBJECT' and f['confidence']==1 for f in r['fragments'])
    count=next(x for x in r['takeoffs'] if x['kind']=='BLOCK_COUNT')
    length=next(x for x in r['takeoffs'] if x['kind']=='LAYER_LENGTH' and x['label']=='PIPE')
    assert (count['label'],count['value'],count['unit'],count['review_status'])==('VALVE',2,'EA','PENDING')
    assert count['basis']=='DESIGN_MODEL_OBJECTS' and '才能作为设计净量' in count['scope_note']
    assert length['value']>=5 and length['unit']=='M' and length['calibration']['verified'] is True


def test_dxf_same_block_name_on_multiple_layers_has_matching_per_layer_evidence(tmp_path):
    import ezdxf
    path=tmp_path/'multi-layer.dxf';document=ezdxf.new();document.units=4
    document.layers.add('A');document.layers.add('B')
    block=document.blocks.new('VALVE');block.add_circle((0,0),1)
    model=document.modelspace();model.add_blockref('VALVE',(1,1),dxfattribs={'layer':'A'})
    model.add_blockref('VALVE',(2,2),dxfattribs={'layer':'B'});document.saveas(path)
    result=parse_file(path,path.name)
    counts=[item for item in result['takeoffs'] if item['kind']=='BLOCK_COUNT']
    assert [(item['layer'],item['value']) for item in counts]==[('A',1),('B',1)]
    for item in counts:
        evidence=result['fragments'][item['source_fragment_index']]['text']
        assert f'CAD layer: {item["layer"]}' in evidence and 'Block inserts: VALVE=1' in evidence


def test_malformed_dxf_is_a_traceable_cad_capability_failure(tmp_path):
    path=tmp_path/'broken.dxf';path.write_text('this is not a DXF document',encoding='utf-8')
    result=parse_file(path,path.name)
    assert result['status']=='PARTIAL' and result['cad_level']=='UNAVAILABLE'
    assert result['takeoffs']==[] and result['fragments']==[]
    assert result['warnings']==['CAD对象解析不可用：OSError。原文件未被修改。']


def test_dwg_conversion_with_malformed_derived_dxf_is_traceable(tmp_path,monkeypatch):
    """A bad converter result must not escape into parser_worker FAILED."""
    import tempfile
    from app.cad import parse_cad
    source=tmp_path/'input.dwg';source.write_bytes(b'unchanged-source')
    holder=tempfile.TemporaryDirectory();derived=Path(holder.name)/'derived.dxf'
    derived.write_text('not a DXF',encoding='utf-8')
    monkeypatch.setattr('app.cad._convert_dwg',lambda _: (derived,holder,'Synthetic converter'))
    result=parse_cad(source,source.name)
    assert result['status']=='PARTIAL' and result['cad_level']=='UNAVAILABLE'
    assert result['warnings']==['CAD对象解析不可用：OSError。原文件未被修改。']
    assert source.read_bytes()==b'unchanged-source'


def test_cad_measurement_formula_inputs_exclude_text_and_insert_handles(tmp_path):
    import ezdxf
    path=tmp_path/'mixed.dxf';document=ezdxf.new();document.units=6
    document.layers.add('PIPE');model=document.modelspace()
    line=model.add_line((0,0),(3,4),dxfattribs={'layer':'PIPE'})
    text=model.add_text('NOT A LENGTH',dxfattribs={'layer':'PIPE'})
    block=document.blocks.new('VALVE');block.add_circle((0,0),1)
    insert=model.add_blockref('VALVE',(1,1),dxfattribs={'layer':'PIPE'})
    document.saveas(path)
    result=parse_file(path,path.name)
    length=next(item for item in result['takeoffs'] if item['kind']=='LAYER_LENGTH')
    assert length['formula']=='SUM_ENTITY_LENGTHS' and length['value']==pytest.approx(5)
    assert length['entity_ids']==[line.dxf.handle]
    assert text.dxf.handle not in length['entity_ids'] and insert.dxf.handle not in length['entity_ids']

def test_parser_worker_progress_write_is_atomic(tmp_path):
    destination=tmp_path/'progress.json';result={'status':'PARTIAL','fragments':[],'pages':[],'warnings':[]}
    save_result(destination,result)
    assert destination.read_text(encoding='utf-8')
    assert not destination.with_name(destination.name+'.tmp').exists()
