"""Selected local email attachments stay inert until an explicit import."""
import hashlib
import sys
from email.message import EmailMessage
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

from tests.app.conftest import upload


def email_with_attachments() -> bytes:
    message=EmailMessage();message['Subject']='RFI 42 response package'
    message.set_content('See the attached response and calculation.')
    message.add_attachment(b'%PDF-synthetic',maintype='application',subtype='pdf',filename='../response.pdf')
    message.add_attachment('plain note',subtype='plain',filename='note.txt')
    return message.as_bytes()


def test_selected_email_attachment_import_reuses_hash_and_dedupe(client,project):
    source=upload(client,project['id'],'response.eml',email_with_attachments())
    before=client.get(f'/api/projects/{project["id"]}/manifest').json()
    listed=client.get(f'/api/documents/{source["document_id"]}/email-attachments')

    assert len(before['documents'])==1 and listed.status_code==200
    attachments=listed.json()['attachments'];pdf=attachments[0]
    assert pdf=={'attachment_index':0,'name':'response.pdf','content_type':'application/pdf',
                 'size':14,'sha256':hashlib.sha256(b'%PDF-synthetic').hexdigest(),'importable':True}
    assert '%PDF-synthetic' not in listed.text

    payload={'document_id':source['document_id'],'attachment_index':0,'expected_sha256':pdf['sha256']}
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json=payload)
    duplicate=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json=payload)
    manifest=client.get(f'/api/projects/{project["id"]}/manifest').json()

    assert imported.status_code==201 and imported.json()['state']=='COMPLETE'
    assert duplicate.status_code==201 and duplicate.json()['state']=='DUPLICATE'
    assert [item['name'] for item in manifest['documents']]==['response.eml','response.pdf']
    imported_uploads=[item for item in manifest['uploads'] if item['name']=='response.pdf']
    assert len(imported_uploads)==2
    assert all(item['source_document_id']==source['document_id'] for item in imported_uploads)
    assert all(item['source_document_name']=='response.eml' for item in imported_uploads)
    assert all(item['source_kind']=='EMAIL_ATTACHMENT' for item in imported_uploads)
    assert all(item['source_detail']=={'attachment_index':0,'content_type':'application/pdf',
                                       'sha256':pdf['sha256']} for item in imported_uploads)


def test_selected_msg_attachment_uses_the_same_bounded_import_path(client,project,monkeypatch):
    data=b'%PDF-from-msg'
    attachment=SimpleNamespace(file_name='../response.pdf',mime_type='application/pdf',file_bytes=data)
    class Message:
        @classmethod
        def load(cls,path):return SimpleNamespace(attachments=(attachment,))
    monkeypatch.setitem(sys.modules,'oxmsg',SimpleNamespace(Message=Message))
    source=upload(client,project['id'],'response.msg',b'synthetic-msg-container')

    listing=client.get(f'/api/documents/{source["document_id"]}/email-attachments')
    item=listing.json()['attachments'][0]
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,'expected_sha256':item['sha256']})

    assert listing.status_code==200 and item=={
        'attachment_index':0,'name':'response.pdf','content_type':'application/pdf','size':len(data),
        'sha256':hashlib.sha256(data).hexdigest(),'importable':True}
    assert imported.status_code==201 and imported.json()['name']=='response.pdf'


def test_email_attachment_import_is_project_scoped_and_identity_checked(client,project):
    source=upload(client,project['id'],'response.eml',email_with_attachments())
    expected=hashlib.sha256(b'%PDF-synthetic').hexdigest()
    other=client.post('/api/projects',json={'name':'Other project'}).json()
    cross=client.post(f'/api/projects/{other["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,'expected_sha256':expected})
    changed=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,'expected_sha256':'0'*64})
    no_identity=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0})
    missing=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':99,'expected_sha256':expected})

    assert cross.status_code==404 and changed.status_code==409 and no_identity.status_code==422 and missing.status_code==404
    assert client.get(f'/api/projects/{other["id"]}/manifest').json()['documents']==[]


def test_email_attachment_endpoint_rejects_non_email_document(client,project):
    source=upload(client,project['id'],'note.txt',b'not an email')
    response=client.get(f'/api/documents/{source["document_id"]}/email-attachments')
    assert response.status_code==422


def test_attached_email_requires_explicit_second_import_before_nested_attachment_is_visible(client,project):
    nested=EmailMessage();nested['Subject']='Nested response';nested.set_content('Nested body')
    nested.add_attachment(b'INNER',maintype='application',subtype='pdf',filename='inner.pdf')
    outer=EmailMessage();outer['Subject']='Forwarded package';outer.set_content('Outer body')
    outer.add_attachment(nested,filename='forwarded.eml')
    source=upload(client,project['id'],'outer.eml',outer.as_bytes())

    first=client.get(f'/api/documents/{source["document_id"]}/email-attachments').json()['attachments']
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,'expected_sha256':first[0]['sha256']})
    nested_document=imported.json()['document_id']
    second=client.get(f'/api/documents/{nested_document}/email-attachments').json()['attachments']

    assert [item['name'] for item in first]==['forwarded.eml']
    assert [item['name'] for item in second]==['inner.pdf']


def test_unmarked_forwarded_message_is_listed_and_requires_explicit_import(client,project):
    nested=EmailMessage();nested['Subject']='Nested response';nested.set_content('Nested body')
    outer=EmailMessage();outer['Subject']='Forwarded package';outer.set_content('Outer body')
    outer.make_mixed();outer.attach(MIMEMessage(nested))
    source=upload(client,project['id'],'unmarked-forward.eml',outer.as_bytes())

    attachments=client.get(
        f'/api/documents/{source["document_id"]}/email-attachments').json()['attachments']
    item=attachments[0]
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,
        'expected_sha256':item['sha256']})

    assert item['name']=='attached-message-1.eml' and item['content_type']=='message/rfc822'
    assert item['importable'] is True and imported.status_code==201
    assert imported.json()['name']=='attached-message-1.eml'


def test_related_non_root_resource_is_listed_and_requires_explicit_import(client,project):
    message=MIMEMultipart('related',start='<body@example.test>')
    message['Subject']='Submittal 23 09 23'
    resource=MIMEText('RESOURCE-ONLY supporting note','plain')
    resource['Content-ID']='<resource@example.test>'
    body=MIMEText('<p>Status: Approved as Noted</p>','html')
    body['Content-ID']='<body@example.test>'
    message.attach(resource);message.attach(body)
    source=upload(client,project['id'],'related-resource.eml',message.as_bytes())

    attachments=client.get(
        f'/api/documents/{source["document_id"]}/email-attachments').json()['attachments']
    item=attachments[0]
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,
        'expected_sha256':item['sha256']})

    assert item['name']=='attachment-1' and item['content_type']=='text/plain'
    assert item['importable'] is True and imported.status_code==201
    assert imported.json()['name']=='attachment-1'


def test_analysis_workflow_shows_selected_attachment_without_inheriting_email_authority(client,project):
    message=EmailMessage();message['Subject']='RFI 77 response package'
    message.set_content('Response:\nSee the selected attachment.')
    message.add_attachment('Attachment content',subtype='plain',filename='response-note.txt')
    source=upload(client,project['id'],'rfi-77.eml',message.as_bytes())
    attachment=client.get(f'/api/documents/{source["document_id"]}/email-attachments').json()['attachments'][0]
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,
        'expected_sha256':attachment['sha256']})
    assert imported.status_code==201

    rid=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()['id']
    db=client.app.state.db;runner=client.app.state.runner
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(rid,))
    run=runner.get(rid);run['model']='mock'
    for document_id in run['document_ids']:runner.parse_one(run,document_id)
    before=db.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    response=client.get(f'/api/analysis-runs/{rid}/workflows')
    after=db.one('SELECT COUNT(*) AS n FROM model_calls')['n']

    item=next(value for value in response.json()['items'] if value['kind']=='EMAIL_ATTACHMENT')
    assert response.status_code==200 and before==after
    assert item['state']=='LINKED' and item['identifier'] is None
    assert [member['file_name'] for member in item['members']]==['rfi-77.eml','response-note.txt']
    assert all(member['status'] is None for member in item['members'])
    assert response.json()['summary']['email_attachment_links']==1
