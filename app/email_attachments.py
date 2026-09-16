"""Safe, user-selected RFC email attachment inspection and local import."""
from __future__ import annotations

import hashlib
import hmac
from email import policy
from email.parser import BytesParser
from pathlib import Path

from app.db import DomainError


MAX_EMAIL_BYTES=8_000_000


def _message(path: Path, original_name: str):
    if Path(original_name).suffix.casefold()!='.eml':raise DomainError('The selected document is not an EML email',422)
    if path.stat().st_size>MAX_EMAIL_BYTES:raise DomainError('Email exceeds the local 8 MB inspection limit',413)
    try:return BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    except (LookupError,TypeError,ValueError) as exc:raise DomainError('The EML email could not be parsed',422) from exc


def _attachment_bytes(part) -> bytes | None:
    try:data=part.get_payload(decode=True)
    except (LookupError,TypeError,ValueError):data=None
    if isinstance(data,bytes):return data
    if part.get_content_type()=='message/rfc822':
        nested=part.get_payload()
        if isinstance(nested,list) and nested:return nested[0].as_bytes(policy=policy.default)
    try:content=part.get_content()
    except (LookupError,TypeError,UnicodeError,ValueError):return None
    if isinstance(content,bytes):return content
    if isinstance(content,str):return content.encode(part.get_content_charset() or 'utf-8',errors='replace')
    return None


def _attachment_name(part,index: int) -> str:
    raw=str(part.get_filename() or '').replace('\\','/').split('/')[-1]
    raw=''.join(char for char in raw if ord(char)>=32).strip()
    if not raw:raw=f'attached-message-{index+1}.eml' if part.get_content_type()=='message/rfc822' else f'attachment-{index+1}'
    return raw[:240]


def _attachment_parts(part):
    if part.get_content_disposition()=='attachment' or part.get_filename():
        yield part
        return
    if part.is_multipart():
        for child in part.iter_parts():yield from _attachment_parts(child)


def _records(path: Path,original_name: str) -> list[tuple[dict,bytes | None]]:
    result=[]
    for part in _attachment_parts(_message(path,original_name)):
        data=_attachment_bytes(part);index=len(result);name=_attachment_name(part,index)
        result.append(({'attachment_index':index,'name':name,'content_type':part.get_content_type(),
                        'size':len(data) if data is not None else None,
                        'sha256':hashlib.sha256(data).hexdigest() if data is not None else None,
                        'importable':data is not None},data))
    return result


def list_email_attachments(path: Path,original_name: str) -> list[dict]:
    return [metadata for metadata,_ in _records(path,original_name)]


def import_email_attachment(uploads,doc: dict,attachment_index: int,expected_sha256: str) -> dict:
    records=_records(uploads.object_path(doc),doc['name'])
    if attachment_index<0 or attachment_index>=len(records):raise DomainError('Email attachment was not found',404)
    metadata,data=records[attachment_index]
    if data is None:raise DomainError('This email attachment encoding is not supported',422)
    if not hmac.compare_digest(expected_sha256,metadata['sha256']):
        raise DomainError('Email attachment identity changed; refresh the attachment list',409)
    return uploads.import_bytes(doc['project_id'],metadata['name'],data,
                                source_document_id=doc['id'],source_kind='EMAIL_ATTACHMENT',
                                source_detail={'attachment_index':attachment_index,
                                               'content_type':metadata['content_type'],
                                               'sha256':metadata['sha256']})
