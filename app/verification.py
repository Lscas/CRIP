"""Server-owned field citations and evidence checks. No network on reads or review edits.

Parser/OCR citations are slices of immutable extracted text, not quotations re-written by a model.
Vision output is retained separately as a whole-page review cue: it is never an original-document
quotation and cannot be used as semantic support. A literal match is NOT semantic support.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import time
from typing import Any

from app.db import Database, DomainError, BudgetError, dumps, now, uid
from app.gateway import InvalidModelOutput, ProviderPaused
from app.settings import ROOT
from contracts.runtime_rules import EvidenceScope, evidence_references, validate_schema

POLICY = 'evidence-check-2'
MAX_BATCH_FIELDS = 4


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def raw_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def statement_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Sentence/line window; decimal points are not sentence delimiters."""
    boundaries = [0]
    for match in re.finditer(r'\n|[。！？!?]|(?<!\d)\.(?=\s|$)|(?<=\d)\.(?=\s|$)', text):
        boundaries.append(match.end())
    left = max((x for x in boundaries if x <= start), default=0)
    right = min((x for x in boundaries if x >= end), default=len(text))
    if right < end:
        right = len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    return left, right


def citation(evidence: dict, start: int, end: int, *, role='CONTEXT', granularity='SENTENCE_OR_LINE') -> dict:
    text = evidence['raw_text']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise DomainError('引用文字范围无效')
    is_vision = (evidence.get('content_basis') == 'MODEL_VISION_OUTPUT'
                 or evidence.get('extraction_method') == 'VISION')
    # Do not let a span inside generated visual narration look like an original sentence.
    # The only permitted vision citation is a page-level context pointer plus the retained
    # output, with human review of the source image required.
    if is_vision:
        start, end = 0, len(text)
        role, granularity, text_basis = 'CONTEXT', 'PAGE_VISUAL_CONTEXT', 'MODEL_VISION_OUTPUT'
    else:
        text_basis = 'PARSER_TEXT'
    loc = deepcopy(evidence['locator'])
    if not is_vision and loc.get('text_line_start') is not None:
        base = loc['text_line_start']
        loc['text_line_start'] = base + text[:start].count('\n')
        loc['text_line_end'] = base + text[:end].count('\n')
    boxes = [] if is_vision else [w['bbox'] for w in evidence.get('text_map', []) if w['end'] > start and w['start'] < end]
    if boxes:
        loc['bbox'] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return {
        'citation_id': 'CITE-' + digest([evidence['evidence_id'], raw_hash(text), start, end])[:24],
        'evidence_id': evidence['evidence_id'], 'document_id': evidence['document_id'],
        'file_name': evidence.get('file_name', evidence['document_id']),
        'file_sha256': evidence['file_sha256'], 'fragment_sha256': raw_hash(text),
        'revision_label': evidence.get('revision_label'), 'internal_revision_date': evidence.get('internal_revision_date'),
        'start': start, 'end': end, 'quote': text[start:end], 'locator': loc,
        'word_boxes': boxes, 'role': role, 'granularity': granularity,
        'text_basis': text_basis,
    }


def exact_quote(evidence: dict, quote: str, role='SUPPORT') -> dict:
    """Reject invented, modified, and ambiguous quotations. Never fuzzy-match a quote."""
    if evidence.get('content_basis') == 'MODEL_VISION_OUTPUT' or evidence.get('extraction_method') == 'VISION':
        raise DomainError('视觉模型输出不是原始文件原句，不能作为语义支持引用')
    if not isinstance(quote, str) or not quote.strip():
        raise DomainError('原句不能为空')
    text = evidence['raw_text']
    start = text.find(quote)
    if start < 0:
        raise DomainError('引用原句与保存的原文不一致')
    if text.find(quote, start + 1) >= 0:
        raise DomainError('引用原句重复，不能唯一定位')
    return citation(evidence, start, start + len(quote), role=role)


def anchors(claim: str, evidence: dict) -> list[dict]:
    """Candidate locations only. Presence in a sentence does not establish support."""
    text = evidence['raw_text']
    if not text:
        return []
    if evidence.get('content_basis') == 'MODEL_VISION_OUTPUT' or evidence.get('extraction_method') == 'VISION':
        return [citation(evidence, 0, len(text), role='CONTEXT', granularity='PAGE_VISUAL_CONTEXT')]
    hits = list(re.finditer(re.escape(claim), text, re.I)) if claim else []
    spans = list(dict.fromkeys(statement_span(text, m.start(), m.end()) for m in hits))
    if not spans:
        return [citation(evidence, 0, len(text), granularity='FRAGMENT_CONTEXT')]
    return [citation(evidence, a, b) for a, b in spans[:3]]


def fields_for(record: dict) -> list[dict]:
    """Every factual leaf is checked, or explicitly labelled a non-document claim."""
    c = record['candidate']; kind = record['kind']; fields = []
    ids = sorted(evidence_references(c))
    root_basis = 'INFERRED' if c.get('requirement_status') == 'INFERRED_TO_VERIFY' else 'DIRECT'
    context = {k: c.get(k) for k in ('name', 'subject', 'activity', 'entity_ids', 'location', 'condition', 'selected_option_ids')}

    def add(path, value, refs=None, basis=None, label=None):
        if value is None or value == '' or value == []:
            return
        fields.append({'path': path, 'claim': str(value), 'label': label or path,
                       'evidence_ids': list(dict.fromkeys(refs if refs is not None else c.get('field_evidence', {}).get(path.lstrip('/'), ids))),
                       'basis': basis or root_basis, 'context': context})

    if kind in ('MATERIAL', 'INSPECTION'):
        for i, value in enumerate(c.get('entity_ids', [])):
            add(f'/entity_ids/{i}', value)
        for i, value in enumerate(c.get('csi_sections', [])):
            add(f'/csi_sections/{i}', value, basis='CLASSIFICATION')
        add('/location', c.get('location'))
        add('/condition', c.get('condition'))
    if kind == 'MATERIAL':
        add('/name', c['name'])
        add('/material_kind', c['material_kind'], basis='CLASSIFICATION')
        for i, p in enumerate(c.get('design_properties', [])):
            add(f'/design_properties/{i}/value', p['value'] + (' ' + p['unit'] if p.get('unit') else ''), p['evidence_ids'], label=p['name'])
        for i, opt in enumerate(c.get('options', [])):
            for name in ('manufacturer', 'product', 'model_number', 'approval_text', 'role'):
                add(f'/options/{i}/{name}', opt.get(name), opt.get('field_evidence',{}).get(name,opt.get('evidence_ids', ids)))
        if c.get('option_relation') != 'NONE':
            add('/option_relation', c.get('option_relation'))
            for i, value in enumerate(c.get('selected_option_ids', [])):
                add(f'/selected_option_ids/{i}', value)
        q = c.get('quantity')
        if q:
            basis = 'DIRECT' if q['method'] in ('EXPLICIT_DOCUMENT', 'SCHEDULE_EXTRACTION') else 'CALCULATED'
            add('/quantity', dumps(q), q.get('evidence_ids', ids), basis=basis)
        add('/parent_entity_id', c.get('parent_entity_id'))
        if c.get('included_in_parent'):
            add('/included_in_parent', 'true')
    elif kind == 'INSPECTION':
        for name in ('activity', 'requirement', 'timing', 'frequency', 'acceptance_criteria', 'standard_reference',
                     'report_name', 'submission_trigger', 'submission_offset_days', 'performer_as_stated', 'witness_as_stated'):
            add('/' + name, c.get(name))
        add('/qa_type', c['qa_type'], basis='CLASSIFICATION')
        add('/standard_body_available', c.get('standard_body_available'), basis='SEARCH_RECORD')
        # Any still-unmapped properties in the support note are not presented as verified facts.
        if '原子属性：' in c.get('support_note', ''):
            add('/unmapped_properties', c['support_note'].split('原子属性：', 1)[1], basis='INFERRED')
    elif kind == 'CONFLICT':
        add('/subject', c['subject'])
        for i, cl in enumerate(c['claims']):
            add(f'/claims/{i}/normalized_value', cl['normalized_value'], [cl['evidence_id']])
        add('/resolution_status', c['resolution_status'], ids, basis='REVISION_POLICY')
        add('/reason', c['reason'], ids, basis='REVISION_POLICY')
        add('/severity_candidate', c.get('severity_candidate'), ids, basis='INFERRED')
        add('/selected_evidence_id', c.get('selected_evidence_id'), ids, basis='REVISION_POLICY')
    else:
        for name in ('subject', 'missing_field_or_document', 'blocking_reason', 'recommended_action'):
            add('/' + name, c.get(name), c.get('related_evidence_ids', []), basis='SEARCH_RECORD')
    if c.get('support_note'):
        add('/support_note', c['support_note'], ids, basis='INFERRED')
    if c.get('inference_rule_id'):
        add('/inference_rule_id', c['inference_rule_id'], ids, basis='INFERRED')
    return fields


def fingerprint(evs: dict) -> str:
    return digest({eid: {k: ev.get(k) for k in ('raw_text', 'content_basis', 'extraction_method', 'file_sha256', 'internal_revision_date', 'revision_label', 'locator', 'text_map')}
                   for eid, ev in sorted(evs.items())})


def summarize(report: dict) -> dict:
    states = [f['status'] for f in report['fields']]
    if 'CONTRADICTED' in states:
        overall = 'CONTRADICTED'
    elif 'UNSUPPORTED' in states:
        overall = 'UNSUPPORTED'
    elif states and all(s in ('SUPPORTED', 'NON_DOCUMENT') for s in states) and 'SUPPORTED' in states:
        overall = 'SUPPORTED'
    elif 'SUPPORTED' in states:
        overall = 'PARTIAL'
    elif states and all(s == 'NON_DOCUMENT' for s in states):
        overall = 'NON_DOCUMENT'
    else:
        overall = 'PENDING'
    report['status'] = overall
    report['counts'] = {s: states.count(s) for s in sorted(set(states))}
    return report


class VerificationService:
    def __init__(self, db: Database, settings, gateway):
        self.db = db; self.s = settings; self.gateway = gateway

    def _evidence_bundle(self, run_id: str, project_id: str, refs: set[str], connection=None) -> dict:
        """Load only evidence referenced by one record, using the evidence PK."""
        result = {}
        storage_ids = [run_id + ':' + evidence_id for evidence_id in sorted(refs)]
        for offset in range(0, len(storage_ids), 400):
            batch = storage_ids[offset:offset + 400]
            placeholders = ','.join('?' for _ in batch)
            query = f'''SELECT e.payload,d.name AS file_name
                        FROM evidence e JOIN documents d
                          ON d.id=e.document_id AND d.project_id=?
                        WHERE e.run_id=? AND e.id IN ({placeholders})'''
            params = (project_id, run_id, *batch)
            rows = ([dict(row) for row in connection.execute(query, params).fetchall()]
                    if connection is not None else self.db.all(query, params))
            for row in rows:
                evidence = json.loads(row['payload'])
                if evidence['evidence_id'] in refs:
                    evidence['file_name'] = row['file_name']
                    result[evidence['evidence_id']] = evidence
        return result

    def load(self, record_id: str):
        row = self.db.one('SELECT * FROM records WHERE id=?', (record_id,))
        record = json.loads(row['envelope'])
        refs = evidence_references(record['candidate'])
        evs = self._evidence_bundle(row['run_id'], row['project_id'], refs)
        return row, record, evs

    def get(self, record_id: str) -> dict:
        row, record, evs = self.load(record_id)
        saved = self.db.one('SELECT payload FROM verification_reports WHERE record_id=?', (record_id,), False)
        if not saved:
            return {'status': 'NOT_CHECKED', 'record_id': record_id, 'fields': [], 'counts': {}}
        report = json.loads(saved['payload'])
        if report['candidate_hash'] != digest(record['candidate']) or report['evidence_fingerprint'] != fingerprint(evs):
            report['status'] = 'STALE'
            for f in report['fields']:
                f['status'] = 'STALE'
        verifier_identity = self.identity()
        if report.get('verifier_identity') != verifier_identity:
            report['status'] = 'STALE'
            for f in report['fields']: f['status'] = 'STALE'
        return report

    def identity(self):
        return {'provider':self.s.provider,'model_id':self.s.cheap_model,
                'prompt_sha256':raw_hash((ROOT/'prompts/claim-verification/system.md').read_text(encoding='utf-8')),
                'endpoint_sha256':raw_hash(self.s.api_base_url),'policy_version':POLICY}

    def save(self, report: dict) -> bool:
        validate_schema('citation-report', report)
        with self.db.connect(True) as c:
            row = c.execute('SELECT envelope FROM records WHERE id=?', (report['record_id'],)).fetchone()
            if not row or digest(json.loads(row['envelope'])['candidate']) != report['candidate_hash']:
                return False
            # Source payloads are append-only in normal app use; protect against changed snapshots as well.
            record = json.loads(row['envelope']); refs = evidence_references(record['candidate'])
            evs = self._evidence_bundle(record['meta']['analysis_run_id'], record['meta']['project_id'], refs, c)
            if fingerprint(evs) != report['evidence_fingerprint']:
                return False
            c.execute('INSERT INTO verification_events VALUES(?,?,?,?)', (uid('VFY'), report['record_id'], dumps(report), now()))
            c.execute('INSERT OR REPLACE INTO verification_reports VALUES(?,?,?)', (report['record_id'], dumps(report), now()))
        return True

    def refresh(self, record_id: str, allow_model=False, job_id=None) -> dict:
        row, record, evs = self.load(record_id)
        old_row = self.db.one('SELECT payload FROM verification_reports WHERE record_id=?', (record_id,), False)
        old = json.loads(old_row['payload']) if old_row else {}
        bykey = {f['check_key']: f for f in old.get('fields', [])}
        verifier_identity = self.identity()
        scope = EvidenceScope('local', row['project_id'], record['meta']['input_snapshot_id'], evs)
        report = {'version': '1.0', 'policy_version': POLICY, 'record_id': record_id,
                  'candidate_hash': digest(record['candidate']), 'evidence_fingerprint': fingerprint(evs),
                  'checked_at': now(), 'status': 'PENDING', 'counts': {}, 'fields': [],
                  'coverage_note': '核验只证明上传资料对输出的支持程度，不证明现场正确或没有漏项。',
                  'limitations': ['解析文本可能有识别或阅读顺序错误。', '只核查记录关联的证据片段；未进行全项目反证检索。'],
                  'source_run_id': row['run_id'], 'verifier_identity': verifier_identity}
        if record['kind']=='MISSING':
            run_row=self.db.one('SELECT snapshot_id,document_ids,coverage FROM runs WHERE id=?',(row['run_id'],))
            target=record['candidate'].get('subject')
            docs=self.db.all('SELECT document_id,status,summary FROM document_results WHERE run_id=?',(row['run_id'],))
            relevant=[d for d in docs if d['document_id']==target] or docs
            report['processing_basis']={'input_snapshot_id':run_row['snapshot_id'],'uploaded_document_count':len(json.loads(run_row['document_ids'])),
                'processed_document_count':len(docs),'scope':'CURRENT_PARSE_AND_EXTRACTION_ONLY',
                'document_results':[{'document_id':d['document_id'],'status':d['status'],'warnings':json.loads(d['summary']).get('warnings',[])} for d in relevant],
                'note':'这是系统处理范围记录，不是设计文件中的否定句，也不是完整搜索证明。'}
        else:report['processing_basis']=None
        for f in fields_for(record):
            sources = {eid: evs[eid] for eid in f['evidence_ids'] if eid in evs}
            f['check_key'] = digest([POLICY, verifier_identity, f, fingerprint(sources)])
            previous = bykey.get(f['check_key'])
            if previous and previous['status'] in ('SUPPORTED', 'NON_DOCUMENT', 'CONTRADICTED', 'UNSUPPORTED'):
                report['fields'].append(deepcopy(previous)); continue
            f.update(status='NEEDS_SEMANTIC', method='LITERAL_LOCATION_ONLY', citations=[], issues=[], request_id=None)
            try:
                scope.check(f['evidence_ids'])
            except ValueError:
                f.update(status='UNSUPPORTED', method='INVALID_REFERENCE'); f['issues'].append('引用不存在或不属于本项目快照。')
                report['fields'].append(f); continue
            for e in sources.values():
                f['citations'].extend(anchors(f['claim'], e))
            if f['basis'] != 'DIRECT':
                f.update(status='NON_DOCUMENT', method=f['basis'])
                f['issues'].append('这是分类、推导、计算或处理记录，不是设计原句；仍须人工核验。')
                if f['basis'] == 'SEARCH_RECORD':
                    f['issues'].append('范围限于本次成功解析的上传资料；未找到不等于设计未规定。')
            elif not sources:
                f.update(status='UNSUPPORTED', method='NO_SOURCE'); f['issues'].append('没有文档证据，不能确认。')
            elif any(e['extraction_method'] in ('OCR', 'VISION') for e in sources.values()):
                f.update(status='NEEDS_CONTEXT', method='VISUAL_REVIEW_REQUIRED')
                if any(e['extraction_method'] == 'VISION' for e in sources.values()):
                    f['issues'].append('视觉模型输出只保留为整页定位和人工审查提示；它不是原始文件原句，不能作为支持或反对结论。')
                else:
                    f['issues'].append('图像识别文字需要原图人工复验。')
            # In mock mode, no semantic model is available. Do not create synthetic verifier success.
            report['fields'].append(f)
        summarize(report); self.save(report)
        if not allow_model or self.s.provider == 'mock':
            return report
        run = self.db.one('SELECT * FROM runs WHERE id=?', (row['run_id'],))
        pending = [f for f in report['fields'] if f['status'] == 'NEEDS_SEMANTIC']
        for offset in range(0, len(pending), MAX_BATCH_FIELDS):
            if job_id and self.db.one("SELECT id FROM verification_jobs WHERE id=? AND state='RUNNING'",(job_id,),False) is None:
                raise ProviderPaused('核验任务已停止；不发送新请求',409)
            current = self.db.one('SELECT envelope FROM records WHERE id=?', (record_id,))
            if digest(json.loads(current['envelope'])['candidate']) != report['candidate_hash']:
                return self.get(record_id)
            batch = pending[offset:offset + MAX_BATCH_FIELDS]
            batch_ids = set(eid for f in batch for eid in f['evidence_ids'])
            bundle = {eid: evs[eid] for eid in batch_ids if eid in evs}
            try:
                result = self.gateway.verify_claims(run, batch, bundle, job_id=job_id)
                self.apply_model(batch, result.data, bundle, result.request_id)
            except InvalidModelOutput:
                for f in batch:
                    f.update(status='NEEDS_CONTEXT', method='MODEL_OUTPUT_REJECTED')
                    f['issues'].append('模型核验响应不完整、过长或引用不精确；不标记通过。')
            except (BudgetError, ProviderPaused):
                report['checked_at'] = now(); summarize(report); self.save(report)
                raise
            report['checked_at'] = now(); summarize(report)
            if not self.save(report):
                return self.get(record_id)
        return report

    @staticmethod
    def apply_model(fields: list[dict], data: dict, evs: dict, request_id):
        validate_schema('claim-check-batch', data)
        lookup = {f['path']: f for f in fields}
        rows = data['checks']
        if len(rows) != len(lookup) or {r['path'] for r in rows} != set(lookup):
            raise InvalidModelOutput('核验字段遗漏或重复')
        staged = []
        for row in rows:
            f = deepcopy(lookup[row['path']]); cites = []
            for q in row['citations']:
                if q['evidence_id'] not in f['evidence_ids'] or q['evidence_id'] not in evs:
                    raise InvalidModelOutput('核验引用越界')
                try: cites.append(exact_quote(evs[q['evidence_id']], q['quote'], q['role']))
                except DomainError as exc: raise InvalidModelOutput('核验原句不能精确定位') from exc
            status = row['status']
            if status == 'SUPPORTED' and not any(c['role'] == 'SUPPORT' for c in cites):
                raise InvalidModelOutput('支持结论没有原文')
            if status == 'CONTRADICTED' and not any(c['role'] == 'CONTRADICT' for c in cites):
                raise InvalidModelOutput('反对结论没有原文')
            if status == 'SUPPORTED' and any(c['role'] == 'CONTRADICT' for c in cites):
                raise InvalidModelOutput('支持结果同时包含未解决反证')
            # A strong numeric mismatch cannot be waved away by a fluent verifier.
            nums = set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)', f['claim']))
            cited_nums = set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)', ' '.join(c['quote'] for c in cites)))
            if status == 'SUPPORTED' and nums - cited_nums:
                status = 'NEEDS_CONTEXT'
                row = {**row, 'reason': '数值不能在引用中直接匹配；单位换算或推算需独立计算依据。'}
            f.update(status=status, method='CHEAP_MODEL_SEMANTIC', citations=cites or f['citations'],
                     issues=[row['reason']], request_id=request_id)
            staged.append(f)
        for result in staged:
            target = lookup[result['path']]; target.clear(); target.update(result)

    def enqueue(self, record_id: str, expected_version: int) -> dict:
        row, record, _ = self.load(record_id)
        if row['review_version'] != expected_version:
            raise DomainError('记录已变化，请刷新后核验', 409)
        if self.s.provider == 'mock' or self.s.live_errors():
            raise DomainError('语义核验需要已配置且批准的真实API；模拟模式不伪造核验结果', 409)
        run = self.db.one('SELECT * FROM runs WHERE id=?', (row['run_id'],))
        if run['provider'] != self.s.provider:
            raise DomainError('原运行Provider不一致，请新建分析', 409)
        if time.time() >= run['deadline_epoch']:
            raise DomainError('原运行已过24小时目标；重新分析不会重置项目预算', 409)
        with self.db.connect(True) as c:
            if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone():
                raise DomainError('已有活跃分析，请结束后核验', 409)
            existing = c.execute("SELECT * FROM verification_jobs WHERE record_id=? AND state IN ('QUEUED','RUNNING')", (record_id,)).fetchone()
            if existing:
                return dict(existing)
            if c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (row['project_id'],)).fetchone():
                raise DomainError('项目有待对账请求，不自动重复付费', 409)
            jid = uid('VJOB')
            c.execute('INSERT INTO verification_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                      (jid, record_id, row['run_id'], digest(record['candidate']), 'QUEUED', '', now(), now(), expected_version))
        return self.db.one('SELECT * FROM verification_jobs WHERE id=?', (jid,))

    def process_job(self, job_id: str):
        with self.db.connect(True) as c:
            job = c.execute('SELECT * FROM verification_jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['state'] != 'QUEUED': return
            if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone(): return
            if c.execute("SELECT id FROM verification_jobs WHERE state='RUNNING'").fetchone(): return
            c.execute("UPDATE verification_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job_id))
        job = self.db.one('SELECT * FROM verification_jobs WHERE id=?', (job_id,))
        row, record, _ = self.load(job['record_id'])
        state = 'DONE'; message = ''
        try:
            if digest(record['candidate']) != job['candidate_hash'] or row['review_version'] != job['review_version']:
                state = 'STALE'
            else:
                self.refresh(job['record_id'], allow_model=True, job_id=job_id)
                current = self.db.one('SELECT * FROM records WHERE id=?', (job['record_id'],))
                if digest(json.loads(current['envelope'])['candidate']) != job['candidate_hash']:
                    state = 'STALE'
        except BudgetError:
            state = 'PAUSED_BUDGET'; message = '预算不足，核验未完成。'
        except ProviderPaused as exc:
            state = 'PAUSED_PROVIDER'; message = str(exc)
        except Exception:
            state = 'FAILED'; message = '核验任务失败；已有证据保留，不能标记通过。'
        self.db.execute("""UPDATE verification_jobs SET state=?,message=?,updated_at=?
                         WHERE id=? AND state='RUNNING'""", (state, message, now(), job_id))
