"""Read-only publication diagnostics, separate from immutable human review decisions."""
import json

from app.db import DomainError


def publication_issues(coverage):
    if isinstance(coverage, str):
        coverage = json.loads(coverage)
    return (coverage or {}).get('publication_issues', [])


def record_publication(coverage, record_id, *, status=None, stage=None):
    issues = [issue for issue in publication_issues(coverage)
              if issue.get('blocked_record_id') == record_id]
    failed = status == 'FAILED' and stage == '生成可审核记录与设计差异'
    return {'publication_blocked': bool(issues) or failed, 'publication_issues': issues}


def require_current_publication(coverage, record_id, *, status=None, stage=None):
    if record_publication(coverage, record_id, status=status, stage=stage)['publication_blocked']:
        raise DomainError('Current candidate was not published; the saved record is historical. Resolve publication issues before review or verification.', 409)
