"""Local, append-only human follow-up cases for bounded Reference QA outcomes."""
from __future__ import annotations

import hashlib
import json

from app.db import Database, DomainError, dumps, now, uid
from app.page_selector import COMPLETE_SELECTOR_VERSION
from app.reference_projection_identity import verify_projection_result_identity
from app.reference_projection_followup import (build_projection_followup_proof,
                                               validate_projection_followup_proof)
from app.reference_results import (PROJECTION_RESULT_KIND, ReferenceResultStore,
                                   require_legacy_result)

_STATUSES={'OPEN','NEEDS_INFORMATION','IN_REVIEW','RESOLVED'}
_RESULT_STATUSES={'ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT'}
_PROJECTION_RESULT_STATUSES={'REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'}
_SOURCE_STATUSES=_RESULT_STATUSES|{'FAILED'}
_MAX_CASES_PER_PAGE=100


class _ConnectionReader:
    """Identity checks must observe the case transaction's same snapshot."""
    def __init__(self,connection): self.connection=connection
    def one(self,query,args=(),required=True):
        row=self.connection.execute(query,args).fetchone()
        if row is None and required:raise DomainError('Reference case identity row is missing.',409)
        return None if row is None else dict(row)


def _question(value: str) -> str:
    normalized=' '.join(value.split()) if isinstance(value,str) else ''
    if not 3 <= len(normalized) <= 1000:
        raise DomainError('Reference case question is outside its supported length.')
    return normalized


def _text(value, field: str, maximum: int, *, required: bool=False) -> str:
    if value is None and not required:return ''
    if not isinstance(value,str):raise DomainError(f'Reference case {field} must be text.')
    value=value.strip()
    if (required and not value) or len(value)>maximum:
        raise DomainError(f'Reference case {field} is invalid.')
    return value


class ReferenceCaseStore:
    """Human-owned case state; it never sends model requests or mutates QA results."""
    def __init__(self,db:Database,uploads=None):
        self.db=db
        # Kept for projection follow-up proof construction; daily case reads
        # intentionally do not reopen an uploaded source object.
        self.uploads=uploads

    @staticmethod
    def _state(row:dict)->dict:
        return {'status':row['status'],'assignee':row['assignee'],
                'resolution':row['resolution'],'version':row['version']}

    def _attachments(self,connection,case_id:str)->list[dict]:
        return [dict(row) for row in connection.execute('''SELECT a.document_id,d.name FROM reference_case_attachments a
                              JOIN documents d ON d.id=a.document_id
                              WHERE a.case_id=? ORDER BY a.created_at,a.document_id''',(case_id,))]

    def _history(self,connection,case_id:str)->list[dict]:
        rows=[dict(row) for row in connection.execute('''SELECT id,actor,action,before_json,after_json,note,created_at
                           FROM reference_case_events WHERE case_id=?
                           ORDER BY rowid''',(case_id,))]
        return [{**row,'before':json.loads(row.pop('before_json')),
                 'after':json.loads(row.pop('after_json'))} for row in rows]

    def _latest_note(self,connection,case_id:str)->str:
        row=connection.execute('''SELECT note FROM reference_case_events
                           WHERE case_id=? AND length(trim(note))>0
                           ORDER BY rowid DESC LIMIT 1''',(case_id,)).fetchone()
        return '' if row is None else row['note']

    def _followups(self,connection,case_id:str)->list[dict]:
        rows=[dict(row) for row in connection.execute('''SELECT f.id,f.run_id,f.snapshot_id,f.result_id,f.result_status,f.proof_json,f.created_at,
                                    r.result_kind,r.result_hash
                           FROM reference_case_followups f JOIN reference_results r ON r.id=f.result_id
                           WHERE f.case_id=? ORDER BY f.created_at,f.id''',(case_id,))]
        return [{**row,'followup_id':row.pop('id'),'proof':json.loads(row.pop('proof_json'))} for row in rows]

    def _require_case_links(self,connection,case_id:str)->None:
        """Dispatch persisted links without making projection cases look legacy."""
        rows=connection.execute('''SELECT r.id,r.result_kind,r.result_json,c.evaluation_item_id,
                                          'SOURCE' AS link_scope,NULL AS proof_json
            FROM reference_results r JOIN reference_cases c ON r.id=c.result_id WHERE c.id=?
            UNION ALL SELECT r.id,r.result_kind,r.result_json,c.evaluation_item_id,
                             'EVALUATION' AS link_scope,NULL AS proof_json
            FROM reference_cases c JOIN reference_evaluation_items i ON i.id=c.evaluation_item_id
                JOIN reference_results r ON r.id=i.result_id
                WHERE c.id=?
            UNION ALL SELECT r.id,r.result_kind,r.result_json,NULL,
                             'FOLLOWUP' AS link_scope,f.proof_json
            FROM reference_results r JOIN reference_case_followups f ON r.id=f.result_id WHERE f.case_id=?''',
            (case_id,case_id,case_id)).fetchall()
        for row in rows:
            value=dict(row)
            if value['result_kind']==PROJECTION_RESULT_KIND:
                if value['evaluation_item_id'] is not None:
                    raise DomainError('Reference result kind and payload are inconsistent.',409)
                verify_projection_result_identity(_ConnectionReader(connection),value['id'])
                if value['link_scope']=='FOLLOWUP':
                    followup=connection.execute('''SELECT * FROM reference_case_followups
                                                   WHERE case_id=? AND result_id=?''',
                                                (case_id,value['id'])).fetchone()
                    result=connection.execute('SELECT * FROM reference_results WHERE id=?',(value['id'],)).fetchone()
                    case=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
                    if followup is None or result is None or case is None:
                        raise DomainError('Reference result kind and payload are inconsistent.',409)
                    validate_projection_followup_proof(_ConnectionReader(connection),connection,case_row=dict(case),
                                                       followup_row=dict(followup),result_row=dict(result))
            else:
                if value['link_scope']=='FOLLOWUP':
                    try: proof=json.loads(value['proof_json'])
                    except (TypeError,ValueError,json.JSONDecodeError) as exc:
                        raise DomainError('Reference result kind and payload are inconsistent.',409) from exc
                    if isinstance(proof,dict) and proof.get('proof_version')=='reference-case-projection-followup-proof-1':
                        raise DomainError('Reference result kind and payload are inconsistent.',409)
                require_legacy_result(value)

    @staticmethod
    def _projection_source_key(project_id,run_id,snapshot_id,question_key,result_id,result_hash):
        return hashlib.sha256(dumps(['reference-case-projection-source-1',project_id,run_id,snapshot_id,
                                     question_key,result_id,result_hash]).encode('utf-8')).hexdigest()

    def _verify_source_key(self,connection,case_id:str)->None:
        case=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
        if case is None:raise DomainError('Reference case was not found.',404)
        case=dict(case);identity=case['result_id'] or case['evaluation_item_id']
        if not identity:raise DomainError('Reference case source is inconsistent.',409)
        result=(connection.execute('SELECT * FROM reference_results WHERE id=?',
                                   (case['result_id'],)).fetchone() if case['result_id'] else None)
        if result is not None and result['result_kind']==PROJECTION_RESULT_KIND:
            if case['evaluation_item_id'] is not None:
                raise DomainError('Reference result kind and payload are inconsistent.',409)
            identity_result=verify_projection_result_identity(_ConnectionReader(connection),case['result_id'])
            if (identity_result['project_id']!=case['project_id'] or identity_result['run_id']!=case['run_id']
                    or identity_result['snapshot_id']!=case['snapshot_id']
                    or identity_result['question_key']!=case['question_key']
                    or _question(identity_result['question'])!=_question(case['question'])
                    or identity_result['status']!=case['source_status']
                    or identity_result['result_hash']!=result['result_hash']):
                raise DomainError('Reference case projection source is inconsistent.',409)
            expected=self._projection_source_key(case['project_id'],case['run_id'],case['snapshot_id'],
                                                 case['question_key'],identity,result['result_hash'])
        else:
            if result is not None:require_legacy_result(dict(result))
            expected=hashlib.sha256(dumps([case['project_id'],case['run_id'],case['snapshot_id'],
                                           case['question_key'],identity]).encode('utf-8')).hexdigest()
        if case['source_key']!=expected:raise DomainError('Reference case source key is inconsistent.',409)

    def _public(self,row:dict, *, history:bool=True, connection=None)->dict:
        if connection is None:
            with self.db.connect() as snapshot:
                snapshot.execute('BEGIN')
                return self._public(row,history=history,connection=snapshot)
        self._verify_source_key(connection,row['id'])
        self._require_case_links(connection,row['id'])
        source_result=(connection.execute('SELECT result_kind,result_hash FROM reference_results WHERE id=?',
                                          (row['result_id'],)).fetchone() if row['result_id'] else None)
        value={
                'case_id':row['id'],'project_id':row['project_id'],'run_id':row['run_id'],
                'snapshot_id':row['snapshot_id'],'question':row['question'],
                'case_view_version':'reference-case-view-2',
                'source':{'status':row['source_status'],'result_id':row['result_id'],
                          'evaluation_id':row['evaluation_id'],'item_id':row['evaluation_item_id'],
                          'result_kind':None if source_result is None else source_result['result_kind'],
                          'result_hash':None if source_result is None else source_result['result_hash']},
                'status':row['status'],'assignee':row['assignee'],'resolution':row['resolution'],
                'version':row['version'],'latest_note':self._latest_note(connection,row['id']),
                'attachments':self._attachments(connection,row['id']),
                'followups':self._followups(connection,row['id']),
                'created_at':row['created_at'],'updated_at':row['updated_at'],
        }
        if history:value['history']=self._history(connection,row['id'])
        return value

    def _source(self,connection,project_id:str,run_id:str,question:str,result_id:str|None,
                evaluation_id:str|None,question_id:str|None)->dict:
        run=connection.execute('SELECT id,project_id,snapshot_id FROM runs WHERE id=?',(run_id,)).fetchone()
        if run is None or run['project_id']!=project_id:
            raise DomainError('Reference case run is outside this project.',404)
        result=None
        if result_id is not None:
            result=connection.execute('''SELECT id,project_id,run_id,snapshot_id,question,status,result_kind,result_json,result_hash
                                         FROM reference_results WHERE id=?''',(result_id,)).fetchone()
            if result is not None and result['result_kind']!=PROJECTION_RESULT_KIND:
                require_legacy_result(dict(result))
            if (result is None or result['project_id']!=project_id or result['run_id']!=run_id
                    or result['snapshot_id']!=run['snapshot_id']
                    or result['status'] not in (_PROJECTION_RESULT_STATUSES
                                                if result['result_kind']==PROJECTION_RESULT_KIND else _RESULT_STATUSES)
                    or _question(result['question'])!=question):
                raise DomainError('Reference case result does not match its project, run, snapshot, or question.',409)
            if result['result_kind']==PROJECTION_RESULT_KIND:
                identity=verify_projection_result_identity(self.db,result['id'])
                if (identity['project_id']!=project_id or identity['run_id']!=run_id
                        or identity['snapshot_id']!=run['snapshot_id'] or identity['question_key']!=hashlib.sha256(question.encode()).hexdigest()
                        or identity['status'] not in _PROJECTION_RESULT_STATUSES):
                    raise DomainError('Reference case result does not match its project, run, snapshot, or question.',409)
        item=None
        if (evaluation_id is None) != (question_id is None):
            raise DomainError('Reference case evaluation and item must be linked together.')
        if question_id is not None:
            item=connection.execute('''SELECT e.id AS evaluation_id,e.project_id,e.run_id,e.snapshot_id,
                                              i.id AS item_id,i.question,i.result_id,f.id AS failure_id
                                       FROM reference_evaluations e JOIN reference_evaluation_items i
                                            ON i.evaluation_id=e.id
                                       LEFT JOIN reference_evaluation_failures f ON f.item_id=i.id
                                       WHERE e.id=? AND i.id=?''',(evaluation_id,question_id)).fetchone()
            if (item is None or item['project_id']!=project_id or item['run_id']!=run_id
                    or item['snapshot_id']!=run['snapshot_id'] or _question(item['question'])!=question):
                raise DomainError('Reference case evaluation item does not match its project, run, snapshot, or question.',409)
            if item['result_id'] is None and item['failure_id'] is None:
                raise DomainError('Reference case evaluation item has no terminal outcome.',409)
            if result is not None and item['result_id']!=result_id:
                raise DomainError('Reference case result does not match its evaluation item.',409)
            if item['failure_id'] is not None:
                if result is not None:raise DomainError('A failed evaluation item cannot be linked to a result.',409)
                source_status='FAILED'
            else:
                if result is None:
                    result=connection.execute('''SELECT id,project_id,run_id,snapshot_id,question,status,result_kind,result_json,result_hash
                                                 FROM reference_results WHERE id=?''',(item['result_id'],)).fetchone()
                if result is None:raise DomainError('Reference case evaluation has no saved result.',409)
                require_legacy_result(dict(result))
                source_status=result['status']
                result_id=result['id']
        elif result is not None:
            source_status=result['status']
        else:
            raise DomainError('Reference case requires a saved result or terminal evaluation item.')
        return {'run':dict(run),'result_id':result_id,'evaluation_id':evaluation_id,
                'item_id':question_id,'source_status':source_status,
                'result_kind':None if result is None else result['result_kind'],
                'result_hash':None if result is None else result['result_hash']}

    def _add_attachments(self,connection,case_id:str,project_id:str,attachments)->list[str]:
        if attachments is None:return []
        if not isinstance(attachments,(list,tuple)) or len(attachments)>50:
            raise DomainError('Reference case attachments are invalid.')
        ids=[]
        for document_id in attachments:
            if not isinstance(document_id,str) or not document_id or document_id in ids:
                raise DomainError('Reference case attachment identifiers are invalid.')
            document=connection.execute('SELECT id FROM documents WHERE id=? AND project_id=?',
                                        (document_id,project_id)).fetchone()
            if document is None:raise DomainError('Reference case attachment is outside this project.',409)
            ids.append(document_id)
        timestamp=now()
        for document_id in ids:
            connection.execute('''INSERT OR IGNORE INTO reference_case_attachments(case_id,document_id,created_at)
                                VALUES(?,?,?)''',(case_id,document_id,timestamp))
        return ids

    def create(self,project_id:str,run_id:str,question:str,*,result_id:str|None=None,
               evaluation_id:str|None=None,question_id:str|None=None,actor:str='local-user',
               attachments=(),supplemental_question:str|None=None,note:str|None=None)->dict:
        question=_question(question);actor=_text(actor,'actor',240,required=True)
        note=_text(note,'note',4000);supplemental=_text(supplemental_question,'supplemental question',1000)
        with self.db.connect(True) as connection:
            source=self._source(connection,project_id,run_id,question,result_id,evaluation_id,question_id)
            question_key=hashlib.sha256(question.encode('utf-8')).hexdigest()
            # A terminal evaluation with a result is the same source outcome as
            # that result alone.  Failed items have no result and use their item
            # identity instead, so callers cannot manufacture a failure result.
            source_identity=source['result_id'] or source['item_id']
            source_key=(self._projection_source_key(project_id,run_id,source['run']['snapshot_id'],question_key,
                                                     source_identity,source['result_hash'])
                        if source['result_kind']==PROJECTION_RESULT_KIND else
                        hashlib.sha256(dumps([project_id,run_id,source['run']['snapshot_id'],question_key,
                                              source_identity]).encode('utf-8')).hexdigest())
            existing=connection.execute('SELECT * FROM reference_cases WHERE source_key=?',(source_key,)).fetchone()
            if existing is not None:
                # Idempotency only applies to the immutable source creation.
                # Mutable human input belongs to an optimistic-lock update, not
                # an ambiguous replay that could silently discard it.
                if attachments or supplemental or note:
                    raise DomainError(
                        'Reference case already exists; use its current version to add human input.',409)
                return self._public(dict(existing),connection=connection)
            timestamp=now();case_id=uid('QACASE')
            connection.execute('''INSERT INTO reference_cases(
                id,source_key,project_id,run_id,snapshot_id,question,question_key,result_id,evaluation_id,
                evaluation_item_id,source_status,status,assignee,resolution,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                case_id,source_key,project_id,run_id,source['run']['snapshot_id'],question,question_key,
                source['result_id'],source['evaluation_id'],source['item_id'],source['source_status'],
                'OPEN','', '',0,timestamp,timestamp))
            row=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
            state=self._state(dict(row))
            connection.execute('''INSERT INTO reference_case_events
                (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                (uid('QACASEEVENT'),case_id,actor,'CREATED',dumps({}),dumps(state),note,timestamp))
            added=self._add_attachments(connection,case_id,project_id,attachments)
            if added:
                connection.execute('''INSERT INTO reference_case_events
                    (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (uid('QACASEEVENT'),case_id,actor,'ATTACHMENTS_ADDED',dumps({}),dumps({'document_ids':added}),'',now()))
            if supplemental:
                connection.execute('''INSERT INTO reference_case_events
                    (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (uid('QACASEEVENT'),case_id,actor,'SUPPLEMENTAL_QUESTION',dumps({}),dumps({'question':supplemental}),'',now()))
        return self.get(case_id)

    def get(self,case_id:str)->dict:
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            row=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
            if row is None:raise DomainError('Reference case was not found.',404)
            return self._public(dict(row),connection=connection)

    def list(self,project_id:str,*,status:str|None=None,assignee:str|None=None,run_id:str|None=None,
             limit:int=50,offset:int=0)->dict:
        if status is not None and status not in _STATUSES:raise DomainError('Reference case status is invalid.')
        if type(limit) is not int or type(offset) is not int or limit<1 or offset<0:
            raise DomainError('Reference case pagination is invalid.')
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            if connection.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone() is None:
                raise DomainError('Reference case project was not found.',404)
            where='project_id=?';args=[project_id]
            if status is not None:where+=' AND status=?';args.append(status)
            if assignee is not None:where+=' AND assignee=?';args.append(_text(assignee,'assignee',240))
            if run_id is not None:
                run=connection.execute('SELECT project_id FROM runs WHERE id=?',(run_id,)).fetchone()
                if run is None:raise DomainError('Reference case run was not found.',404)
                if run['project_id']!=project_id:raise DomainError('Reference case run is outside this project.',404)
                where+=' AND run_id=?';args.append(run_id)
            total=connection.execute(f'SELECT COUNT(*) AS n FROM reference_cases WHERE {where}',tuple(args)).fetchone()['n']
            rows=[dict(row) for row in connection.execute(f'''SELECT * FROM reference_cases WHERE {where}
                                 ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?''',
                             (*args,min(limit,_MAX_CASES_PER_PAGE),offset))]
            return {'items':[self._public(row,history=False,connection=connection) for row in rows],
                    'total':total,'limit':min(limit,_MAX_CASES_PER_PAGE),'offset':offset}

    def update(self,case_id:str,expected_version:int,*,status:str|None=None,assignee:str|None=None,
               note:str|None=None,resolution:str|None=None,actor:str='local-user',attachments=(),
               supplemental_question:str|None=None)->dict:
        if type(expected_version) is not int or expected_version<0:raise DomainError('Reference case version is invalid.')
        if status is not None and status not in _STATUSES:raise DomainError('Reference case status is invalid.')
        actor=_text(actor,'actor',240,required=True);note_supplied=note is not None;note=_text(note,'note',4000)
        supplemental=_text(supplemental_question,'supplemental question',1000)
        with self.db.connect(True) as connection:
            row=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
            if row is None:raise DomainError('Reference case was not found.',404)
            row=dict(row)
            self._verify_source_key(connection,case_id)
            self._require_case_links(connection,case_id)
            if row['version']!=expected_version:raise DomainError('Reference case changed; refresh before updating.',409)
            before=self._state(row);new_status=status if status is not None else row['status']
            new_assignee=_text(assignee,'assignee',240) if assignee is not None else row['assignee']
            new_resolution=_text(resolution,'resolution',4000) if resolution is not None else row['resolution']
            reopening=row['status']=='RESOLVED' and new_status!='RESOLVED'
            if reopening:new_resolution=''
            if new_status=='RESOLVED' and not new_resolution:
                raise DomainError('Resolving a Reference case requires a non-empty human resolution.',409)
            changed=(new_status!=row['status'] or new_assignee!=row['assignee']
                     or new_resolution!=row['resolution'] or note_supplied or bool(attachments) or bool(supplemental))
            if changed:
                timestamp=now();version=row['version']+1
                connection.execute('''UPDATE reference_cases SET status=?,assignee=?,resolution=?,version=?,updated_at=?
                                      WHERE id=? AND version=?''',(new_status,new_assignee,new_resolution,version,
                                      timestamp,case_id,expected_version))
                after={'status':new_status,'assignee':new_assignee,'resolution':new_resolution,'version':version}
                action='REOPENED' if reopening else 'UPDATED'
                connection.execute('''INSERT INTO reference_case_events
                    (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (uid('QACASEEVENT'),case_id,actor,action,dumps(before),dumps(after),note,timestamp))
            added=self._add_attachments(connection,case_id,row['project_id'],attachments)
            if added:
                connection.execute('''INSERT INTO reference_case_events
                    (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (uid('QACASEEVENT'),case_id,actor,'ATTACHMENTS_ADDED',dumps({}),dumps({'document_ids':added}),'',now()))
            if supplemental:
                connection.execute('''INSERT INTO reference_case_events
                    (id,case_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (uid('QACASEEVENT'),case_id,actor,'SUPPLEMENTAL_QUESTION',dumps({}),dumps({'question':supplemental}),'',now()))
        return self.get(case_id)

    def link_followup(self,case_id:str,expected_version:int,run_id:str,result_id:str,
                      supplemental_document_ids:list[str],note:str,actor:str='local-user')->dict:
        if type(expected_version) is not int or expected_version<0:raise DomainError('Reference case version is invalid.')
        actor=_text(actor,'actor',240,required=True);note=_text(note,'note',4000)
        if not isinstance(supplemental_document_ids,list) or not supplemental_document_ids:
            raise DomainError('Reference case follow-up requires one or more existing attachments.')
        if len(supplemental_document_ids)>50 or len(set(supplemental_document_ids))!=len(supplemental_document_ids):
            raise DomainError('Reference case follow-up attachment identifiers are invalid.')
        with self.db.connect(True) as connection:
            case=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
            if case is None:raise DomainError('Reference case was not found.',404)
            case=dict(case)
            self._verify_source_key(connection,case_id)
            self._require_case_links(connection,case_id)
            run=connection.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            if run is None or run['project_id']!=case['project_id'] or run['id']==case['run_id'] or run['status'] not in ('PARTIAL','COMPLETED'):
                raise DomainError('Reference case follow-up run is invalid.',409)
            try:mode=json.loads(run['capabilities']).get('analysis_mode','LEGACY_ANALYSIS');document_ids=json.loads(run['document_ids'])
            except (TypeError,ValueError,json.JSONDecodeError) as exc:raise DomainError('Reference case follow-up run metadata is invalid.',409) from exc
            if mode!='REFERENCE_QA' or not isinstance(document_ids,list):raise DomainError('Reference case follow-up must use a terminal Reference QA run.',409)
            result=connection.execute('SELECT * FROM reference_results WHERE id=?',(result_id,)).fetchone()
            if result is not None and result['result_kind']!=PROJECTION_RESULT_KIND:
                require_legacy_result(dict(result))
            if (result is None or result['project_id']!=case['project_id'] or result['run_id']!=run_id
                    or result['snapshot_id']!=run['snapshot_id']
                    or result['status'] not in (_PROJECTION_RESULT_STATUSES if result['result_kind']==PROJECTION_RESULT_KIND else _RESULT_STATUSES)
                    or _question(result['question'])!=_question(case['question'])):
                raise DomainError('Reference case follow-up result does not match its project, run, snapshot, or question.',409)
            is_projection=result['result_kind']==PROJECTION_RESULT_KIND
            if is_projection:
                identity=verify_projection_result_identity(_ConnectionReader(connection),result_id)
                if (identity['project_id']!=case['project_id'] or identity['run_id']!=run_id
                        or identity['snapshot_id']!=run['snapshot_id']
                        or identity['question_key']!=case['question_key']
                        or identity['result_hash']!=result['result_hash']):
                    raise DomainError('Reference case follow-up result is inconsistent.',409)
                payload_hash=hashlib.sha256(dumps([
                    'reference-case-projection-followup-request-1',case_id,run_id,run['snapshot_id'],
                    case['question_key'],result_id,result['result_hash'],sorted(supplemental_document_ids),note,
                ]).encode()).hexdigest()
            else:
                # Deliberately retain the established legacy replay digest.
                payload_hash=hashlib.sha256(dumps([run_id,result_id,sorted(supplemental_document_ids),note]).encode()).hexdigest()
            existing=connection.execute('''SELECT id,payload_hash FROM reference_case_followups
                                           WHERE case_id=? AND result_id=?''',(case_id,result_id)).fetchone()
            if existing is not None:
                if existing['payload_hash']!=payload_hash:
                    raise DomainError('Reference case follow-up result is already linked with different input.',409)
                connection.commit();return self.get(case_id)
            if case['version']!=expected_version:raise DomainError('Reference case changed; refresh before linking.',409)
            if case['status']=='RESOLVED':raise DomainError('Reopen a resolved Reference case before linking a follow-up result.',409)
            # Runner.create freezes every document that existed at creation as
            # sorted (id, sha256) snapshot input.  A follow-up can only prove
            # a new answer if that run still represents the complete project
            # document set that existed when the saved answer was produced.
            answer_documents=[dict(row) for row in connection.execute(
                'SELECT id,sha256 FROM documents WHERE project_id=? AND created_at<=? ORDER BY id',
                (case['project_id'],result['created_at']))]
            answer_document_ids=[row['id'] for row in answer_documents]
            answer_snapshot='SN-'+hashlib.sha256(dumps(answer_documents).encode()).hexdigest()[:32]
            if (document_ids!=answer_document_ids or run['snapshot_id']!=answer_snapshot):
                raise DomainError('Reference case follow-up run omits documents present when its answer was saved.',409)
            attached={row['document_id'] for row in connection.execute('SELECT document_id FROM reference_case_attachments WHERE case_id=?',(case_id,))}
            supplied=set(supplemental_document_ids)
            if not supplied<=attached or not supplied<=set(document_ids):
                raise DomainError('Reference case follow-up documents must be attached to the case and included in the new run.',409)
            linked_at=max([case['created_at']]+[row['created_at'] for row in connection.execute(
                'SELECT created_at FROM reference_case_attachments WHERE case_id=? AND document_id IN (%s)' % ','.join('?'*len(supplied)),
                (case_id,*sorted(supplied)))])
            if run['created_at']<linked_at or result['created_at']<run['created_at']:
                raise DomainError('Reference case follow-up must be created after the case and selected attachments.',409)
            if is_projection:
                if self.uploads is None:
                    raise DomainError('Reference case projection follow-up source authentication is unavailable.',409)
                # The helper is the one source-backed check.  Do not weaken it
                # into an identity-only read merely because a case is human-owned.
                case_pin=dict(case);run_pin=dict(run);result_pin=dict(result)
                attachment_pin=[dict(row) for row in connection.execute('''SELECT a.document_id,a.created_at,d.sha256
                    FROM reference_case_attachments a JOIN documents d ON d.id=a.document_id
                    WHERE a.case_id=? ORDER BY a.document_id''',(case_id,))]
                answer_documents_pin=list(answer_documents)
                connection.commit()
                proof=build_projection_followup_proof(
                    self.db,self.uploads,case_row=case,run_row=run,result_row=result,
                    supplemental_document_ids=sorted(supplied))
                connection.execute('BEGIN IMMEDIATE')
                current=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
                current_run=connection.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
                current_result=connection.execute('SELECT * FROM reference_results WHERE id=?',(result_id,)).fetchone()
                attachment_now=[dict(row) for row in connection.execute('''SELECT a.document_id,a.created_at,d.sha256
                    FROM reference_case_attachments a JOIN documents d ON d.id=a.document_id
                    WHERE a.case_id=? ORDER BY a.document_id''',(case_id,))]
                answer_documents_now=[dict(row) for row in connection.execute(
                    'SELECT id,sha256 FROM documents WHERE project_id=? AND created_at<=? ORDER BY id',
                    (case['project_id'],result['created_at']))]
                existing=connection.execute('SELECT id,payload_hash FROM reference_case_followups WHERE case_id=? AND result_id=?',
                                            (case_id,result_id)).fetchone()
                if existing is not None:
                    if existing['payload_hash']!=payload_hash:
                        raise DomainError('Reference case follow-up result is already linked with different input.',409)
                    connection.commit();return self.get(case_id)
                if (current is None or current_run is None or current_result is None
                        or dict(current)!=case_pin or dict(current_run)!=run_pin or dict(current_result)!=result_pin
                        or attachment_now!=attachment_pin or answer_documents_now!=answer_documents_pin
                        or current['version']!=expected_version or current['status']=='RESOLVED'
                        or current['project_id']!=case['project_id'] or current['run_id']!=case['run_id']
                        or current['snapshot_id']!=case['snapshot_id'] or current['question_key']!=case['question_key']
                        or current_run['project_id']!=run['project_id'] or current_run['snapshot_id']!=run['snapshot_id']
                        or current_result['project_id']!=result['project_id'] or current_result['run_id']!=run_id
                        or current_result['snapshot_id']!=run['snapshot_id'] or current_result['question_key']!=case['question_key']
                        or current_result['result_kind']!=PROJECTION_RESULT_KIND
                        or current_result['result_hash']!=result['result_hash']):
                    raise DomainError('Reference case changed; refresh before linking.',409)
                self._verify_source_key(connection,case_id)
                verify_projection_result_identity(_ConnectionReader(connection),result_id)
                self._require_case_links(connection,case_id)
                attached_now={row['document_id'] for row in connection.execute(
                    'SELECT document_id FROM reference_case_attachments WHERE case_id=?',(case_id,))}
                if not supplied<=attached_now:
                    raise DomainError('Reference case changed; refresh before linking.',409)
                followup_id=uid('QACASEFOLLOW');timestamp=now();version=current['version']+1
                next_status='NEEDS_INFORMATION' if current_result['status']=='NEED_USER_INPUT' else 'IN_REVIEW'
                connection.execute('''INSERT INTO reference_case_followups
                    (id,case_id,run_id,snapshot_id,result_id,result_status,proof_json,payload_hash,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',(followup_id,case_id,run_id,current_run['snapshot_id'],result_id,
                    current_result['status'],dumps(proof),payload_hash,timestamp))
                before=self._state(dict(current));after={'status':next_status,'assignee':current['assignee'],
                    'resolution':current['resolution'],'version':version,'followup_id':followup_id,
                    'proof_sha256':proof['proof_sha256']}
                updated=connection.execute('UPDATE reference_cases SET status=?,version=?,updated_at=? WHERE id=? AND version=?',
                    (next_status,version,timestamp,case_id,expected_version))
                if updated.rowcount!=1:raise DomainError('Reference case changed; refresh before linking.',409)
                connection.execute('''INSERT INTO reference_case_events(id,case_id,actor,action,before_json,after_json,note,created_at)
                    VALUES(?,?,?,?,?,?,?,?)''',(uid('QACASEEVENT'),case_id,actor,'UPDATED',dumps(before),dumps(after),note,timestamp))
                connection.commit()
                return self.get(case_id)
            # Result authentication reads the same SQLite database through the
            # immutable-result store. Release this transaction's reserved lock
            # first; version is rechecked before this method writes anything.
            case_pin=dict(case);run_pin=dict(run);result_pin=dict(result)
            attachment_pin=[dict(row) for row in connection.execute('''SELECT a.document_id,a.created_at,d.sha256
                FROM reference_case_attachments a JOIN documents d ON d.id=a.document_id
                WHERE a.case_id=? ORDER BY a.document_id''',(case_id,))]
            answer_documents_pin=list(answer_documents)
            try:
                raw_receipts=json.loads(result['result_json'])['execution_receipts']
                if (not isinstance(raw_receipts,list) or not 1<=len(raw_receipts)<=3
                        or any(not isinstance(item,dict) for item in raw_receipts)):
                    raise ValueError('invalid receipt list')
                receipt_ids=sorted({item['model_call_id'] for item in raw_receipts})
            except (KeyError,TypeError,ValueError,json.JSONDecodeError):
                raise DomainError('Reference case follow-up receipt is malformed.',409)
            if not receipt_ids or any(not isinstance(value,str) for value in receipt_ids):
                raise DomainError('Reference case follow-up receipt is malformed.',409)
            call_columns=('project_id,run_id,model,state,request_hash,response,'
                          'reference_input_commitment_version,reference_input_commitment_sha256')
            call_pin=[dict(row) for row in connection.execute(
                'SELECT id,%s FROM model_calls WHERE id IN (%s) ORDER BY id' %
                (call_columns,','.join('?'*len(receipt_ids))),tuple(receipt_ids))]
            evidence_pin=[dict(row) for row in connection.execute('''SELECT e.payload,e.document_id,d.name AS file_name,
                d.project_id AS document_project_id FROM evidence e JOIN documents d ON d.id=e.document_id
                WHERE e.run_id=? AND e.project_id=? ORDER BY e.document_id,e.payload''',(run_id,case['project_id']))]
            connection.commit()
            result_json,receipts,sources=ReferenceResultStore(self.db).authenticate_saved_result(dict(run),dict(result))
            if any((receipt.get('receipt_version'),receipt.get('selector_version')) not in (
                   ('reference-model-input-receipt-2',COMPLETE_SELECTOR_VERSION),
                   ('reference-model-input-receipt-3','literal-page-selector-9'))
                   for receipt in receipts):
                raise DomainError('Reference case follow-up requires a complete-source execution receipt.',409)
            # Reacquire the reserved writer lock after immutable-result
            # authentication.  The pre-authentication commit prevents a
            # second SQLite connection from self-deadlocking, but it also
            # means this write must repeat its concurrency checks atomically.
            connection.execute('BEGIN IMMEDIATE')
            existing=connection.execute('''SELECT id,payload_hash FROM reference_case_followups
                                           WHERE case_id=? AND result_id=?''',(case_id,result_id)).fetchone()
            if existing is not None:
                if existing['payload_hash']!=payload_hash:raise DomainError('Reference case follow-up result is already linked with different input.',409)
                connection.commit()
                return self.get(case_id)
            current=connection.execute('SELECT * FROM reference_cases WHERE id=?',(case_id,)).fetchone()
            current_run=connection.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            current_result=connection.execute('SELECT * FROM reference_results WHERE id=?',(result_id,)).fetchone()
            attachment_now=[dict(row) for row in connection.execute('''SELECT a.document_id,a.created_at,d.sha256
                FROM reference_case_attachments a JOIN documents d ON d.id=a.document_id
                WHERE a.case_id=? ORDER BY a.document_id''',(case_id,))]
            answer_documents_now=[dict(row) for row in connection.execute(
                'SELECT id,sha256 FROM documents WHERE project_id=? AND created_at<=? ORDER BY id',
                (case['project_id'],result['created_at']))]
            call_now=[dict(row) for row in connection.execute(
                'SELECT id,%s FROM model_calls WHERE id IN (%s) ORDER BY id' %
                (call_columns,','.join('?'*len(receipt_ids))),tuple(receipt_ids))]
            evidence_now=[dict(row) for row in connection.execute('''SELECT e.payload,e.document_id,d.name AS file_name,
                d.project_id AS document_project_id FROM evidence e JOIN documents d ON d.id=e.document_id
                WHERE e.run_id=? AND e.project_id=? ORDER BY e.document_id,e.payload''',(run_id,case['project_id']))]
            if (current is None or current_run is None or current_result is None
                    or dict(current)!=case_pin or dict(current_run)!=run_pin or dict(current_result)!=result_pin
                    or attachment_now!=attachment_pin or answer_documents_now!=answer_documents_pin or call_now!=call_pin
                    or evidence_now!=evidence_pin
                    or current['version']!=expected_version or current['status']=='RESOLVED'):
                raise DomainError('Reference case changed; refresh before linking.',409)
            self._verify_source_key(connection,case_id)
            self._require_case_links(connection,case_id)
            require_legacy_result(dict(current_result))
            visual={item.get('region_id'):item for item in result_json.get(
                'sent_visual_regions',result_json.get('visual_regions',[])) if isinstance(item,dict)}
            evidence_documents={evidence_id:source['document_id'] for evidence_id,source in sources.items()}
            proofs=[]
            for document_id in sorted(supplied):
                text_rounds=[];visual_rounds=[];citation_count=connection.execute('''SELECT COUNT(*) AS n FROM reference_result_citations
                    WHERE result_id=? AND document_id=?''',(result_id,document_id)).fetchone()['n']
                for receipt in receipts:
                    if not isinstance(receipt,dict):raise DomainError('Reference case follow-up receipt is malformed.',409)
                    call=connection.execute('''SELECT state,project_id,run_id,request_hash FROM model_calls
                                               WHERE id=?''',(receipt.get('model_call_id'),)).fetchone()
                    if (call is None or call['state']!='SETTLED' or call['project_id']!=case['project_id']
                            or call['run_id']!=run_id or call['request_hash']!=receipt.get('request_hash')):
                        raise DomainError('Reference case follow-up receipt is not a settled model call for this run.',409)
                    round_number=receipt.get('round')
                    inputs=receipt.get('evidence_inputs',[]);images=receipt.get('visual_inputs',[])
                    if type(round_number) is not int or round_number<1 or not isinstance(inputs,list) or not isinstance(images,list):
                        raise DomainError('Reference case follow-up receipt is malformed.',409)
                    for evidence in inputs:
                        evidence_id=evidence.get('evidence_id') if isinstance(evidence,dict) else None
                        if evidence_documents.get(evidence_id)==document_id:
                            text_rounds.append(round_number)
                    for image in images:
                        region=visual.get(image.get('region_id') if isinstance(image,dict) else None)
                        if (receipt['receipt_version']=='reference-model-input-receipt-2'
                                and region and region.get('document_id')==document_id):visual_rounds.append(round_number)
                if not text_rounds and not visual_rounds:
                    raise DomainError('Reference case follow-up attachment was not sent to the model.',409)
                document=connection.execute('''SELECT sha256 FROM documents
                                                WHERE id=? AND project_id=?''',(document_id,case['project_id'])).fetchone()
                if document is None:raise DomainError('Reference case follow-up document is invalid.',409)
                proof_receipts=[{'call_id':receipt['model_call_id'],'round':receipt['round'],
                                 'request_hash':receipt['request_hash'],'question_hash':receipt['question_hash'],
                                 'provider':receipt['provider'],'model':receipt['model'],
                                 'evidence_inputs':[value for value in receipt['evidence_inputs'] if evidence_documents.get(value['evidence_id'])==document_id],
                                 'visual_inputs':[value for value in receipt['visual_inputs'] if visual.get(value['region_id'],{}).get('document_id')==document_id]} for receipt in receipts]
                proofs.append({'document_id':document_id,'document_sha256':document['sha256'],
                               'text_input_rounds':sorted(set(text_rounds)),
                               'visual_input_rounds':sorted(set(visual_rounds)),'first_input_round':min(text_rounds+visual_rounds),
                               'text_input_count':len(text_rounds),'visual_input_count':len(visual_rounds),
                               'citation_count':citation_count,'receipts':proof_receipts})
            followup_id=uid('QACASEFOLLOW');timestamp=now()
            models_used=[]
            execution_route=[]
            for receipt in receipts:
                model_name=receipt['model']
                if model_name not in models_used:models_used.append(model_name)
                execution_route.append({'round':receipt['round'],'call_id':receipt['model_call_id'],
                                        'model':model_name,
                                        'input_mode':'VISION' if receipt['visual_inputs'] else 'TEXT',
                                        'request_hash':receipt['request_hash']})
            proof={'documents':proofs,'model':result['model'],'terminal_model':receipts[-1]['model'],
                   'models_used':models_used,'execution_route':execution_route,
                   'result_id':result_id,'result_status':result['status'],
                   'result_hash':result['result_hash'],'proof_sha256':''}
            proof['proof_sha256']=hashlib.sha256(dumps({key:value for key,value in proof.items() if key!='proof_sha256'}).encode()).hexdigest()
            connection.execute('''INSERT INTO reference_case_followups
                (id,case_id,run_id,snapshot_id,result_id,result_status,proof_json,payload_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',(followup_id,case_id,run_id,run['snapshot_id'],result_id,result['status'],dumps(proof),payload_hash,timestamp))
            before=self._state(case);next_status='IN_REVIEW' if result['status'] in ('ANSWERED','CANNOT_ANSWER') else 'NEEDS_INFORMATION';version=case['version']+1
            after={'status':next_status,'assignee':case['assignee'],'resolution':case['resolution'],'version':version,'followup_id':followup_id}
            updated=connection.execute('UPDATE reference_cases SET status=?,version=?,updated_at=? WHERE id=? AND version=?',(next_status,version,timestamp,case_id,expected_version))
            if updated.rowcount!=1:raise DomainError('Reference case changed; refresh before linking.',409)
            connection.execute('''INSERT INTO reference_case_events(id,case_id,actor,action,before_json,after_json,note,created_at)
                VALUES(?,?,?,?,?,?,?,?)''',(uid('QACASEEVENT'),case_id,actor,'UPDATED',dumps(before),dumps(after),note,timestamp))
        return self.get(case_id)
