"""Shared standard-library MIME body and attachment boundaries."""
from __future__ import annotations

from collections.abc import Iterator
from email.message import Message


def is_email_attachment(part: Message) -> bool:
    return (part.get_content_disposition()=='attachment' or bool(part.get_filename()) or
            part.get_content_maintype()=='message')


def _related_root(children: tuple[Message,...],start: object) -> Message | None:
    if not children:return None
    target=str(start or '').strip().strip('"')
    if target:
        for child in children:
            if str(child.get('Content-ID') or '').strip()==target:return child
    return children[0]


def email_body_parts(part: Message) -> tuple[Message,...]:
    """Return body candidates, selecting only the RFC multipart/related root."""
    children=tuple(part.iter_parts())
    if part.get_content_type()!='multipart/related':return children
    root=_related_root(children,part.get_param('start'))
    return (root,) if root is not None else ()


def iter_email_attachments(part: Message) -> Iterator[Message]:
    """Yield explicit attachments and non-root related resources without recursion into them."""
    if is_email_attachment(part):
        yield part
        return
    if not part.is_multipart():return
    children=tuple(part.iter_parts())
    if part.get_content_type()=='multipart/related':
        root=_related_root(children,part.get_param('start'))
        if root is not None:yield from iter_email_attachments(root)
        for child in children:
            if child is not root:yield child
        return
    for child in children:yield from iter_email_attachments(child)


def safe_attachment_name(value: object,index: int,content_type: str) -> str:
    raw=str(value or '').replace('\\','/').split('/')[-1]
    raw=''.join(char for char in raw if ord(char)>=32).strip()
    if not raw:
        raw=(f'attached-message-{index+1}.eml' if content_type=='message/rfc822'
             else f'attachment-{index+1}')
    return raw[:240]
