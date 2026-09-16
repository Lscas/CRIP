"""Safe, user-selected local email attachment inspection and import."""
from __future__ import annotations

import hashlib
import hmac
from email import policy
from email.parser import BytesParser
from pathlib import Path

from app.db import DomainError
from app.email_mime import iter_email_attachments,safe_attachment_name


MAX_EMAIL_BYTES=8_000_000


def _message(path: Path):
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


def _eml_records(path: Path) -> list[tuple[dict,bytes | None]]:
    result=[]
    for part in iter_email_attachments(_message(path)):
        data=_attachment_bytes(part);index=len(result);content_type=part.get_content_type()
        name=safe_attachment_name(part.get_filename(),index,content_type)
        result.append(({'attachment_index':index,'name':name,'content_type':content_type,
                        'size':len(data) if data is not None else None,
                        'sha256':hashlib.sha256(data).hexdigest() if data is not None else None,
                        'importable':data is not None},data))
    return result


def _msg_records(path: Path) -> list[tuple[dict,bytes | None]]:
    if path.stat().st_size>MAX_EMAIL_BYTES:raise DomainError('Email exceeds the local 8 MB inspection limit',413)
    from oxmsg import Message
    try:
        attachments=Message.load(str(path)).attachments;result=[]
        for item in attachments:
            index=len(result);raw=item.file_bytes;data=raw if isinstance(raw,bytes) else None
            content_type=str(item.mime_type or 'application/octet-stream')
            name=safe_attachment_name(item.file_name,index,content_type)
            result.append(({'attachment_index':index,'name':name,'content_type':content_type,
                            'size':len(data) if data is not None else None,
                            'sha256':hashlib.sha256(data).hexdigest() if data is not None else None,
                            'importable':data is not None},data))
    except (LookupError,OSError,TypeError,UnicodeError,ValueError) as exc:
        raise DomainError('The Outlook MSG email could not be parsed',422) from exc
    return result


def _records(path: Path,original_name: str) -> list[tuple[dict,bytes | None]]:
    suffix=Path(original_name).suffix.casefold()
    if suffix=='.eml':return _eml_records(path)
    if suffix=='.msg':return _msg_records(path)
    raise DomainError('The selected document is not a supported email file',422)


def list_email_attachments(path: Path,original_name: str) -> list[dict]:
    return [metadata for metadata,_ in _records(path,original_name)]


def import_email_attachments(uploads,doc: dict,selections: list[tuple[int,str]]) -> list[dict]:
    if not selections:raise DomainError('Select at least one email attachment',422)
    records=_records(uploads.object_path(doc),doc['name'])
    selected=[];seen=set()
    for attachment_index,expected_sha256 in selections:
        if attachment_index in seen:
            raise DomainError('Email attachment selection contains a duplicate index',422)
        seen.add(attachment_index)
        if attachment_index<0 or attachment_index>=len(records):
            raise DomainError('Email attachment was not found',404)
        metadata,data=records[attachment_index]
        if data is None:raise DomainError('This email attachment encoding is not supported',422)
        if not hmac.compare_digest(expected_sha256,metadata['sha256']):
            raise DomainError('Email attachment identity changed; refresh the attachment list',409)
        selected.append((metadata,data))
    return [uploads.import_bytes(doc['project_id'],metadata['name'],data,
                                 source_document_id=doc['id'],source_kind='EMAIL_ATTACHMENT',
                                 source_detail={'attachment_index':metadata['attachment_index'],
                                                'content_type':metadata['content_type'],
                                                'sha256':metadata['sha256']})
            for metadata,data in selected]


def import_email_attachment(uploads,doc: dict,attachment_index: int,expected_sha256: str) -> dict:
    return import_email_attachments(uploads,doc,[(attachment_index,expected_sha256)])[0]
