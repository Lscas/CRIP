"""Local immutable Reference QA results, normalized citations and review history."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from jsonschema import ValidationError

from app.db import Database,DomainError,dumps,now,uid
from contracts.runtime_rules import validate_schema
from app.reference_projection_execution import (
    RESULT_KIND as PROJECTION_RESULT_KIND,
    _authenticate_projection_execution_stage,
    authenticate_projection_execution,
    compile_projection_execution,
)
from app.reference_projection_decision import ProjectionDecisionError
from app.reference_projection_review import (
    _build_review_view_from_authenticated_stage,
    _render_review_source_from_authenticated_bytes,
)

MAX_RESULT_BYTES=1_000_000
MAX_RESULT_CITATIONS=48
MAX_EXPORT_RESULTS=500
MAX_EXPORT_BYTES=64*1024*1024
MAX_COMPARISON_RESULTS=2_000
MAX_COMPARISON_QUESTIONS=1_000
MAX_COMPARISON_BYTES=32*1024*1024
_FORBIDDEN_KEYS={'reasoning','analysis','chain_of_thought','thoughts'}
_HASH=re.compile(r'^[0-9a-f]{64}$')
_RESULT_ID=re.compile(r'^QAR-[0-9a-f]{32}$')
_LEGACY_RESULT_KIND='REFERENCE_QA_RESULT'
_PROJECTION_KEY_DOMAIN='projection-result-key-1'
_PROJECTION_MARKERS={'projection_execution_version','result_kind','projection_row_count'}


def _normalized_question(value:str)->str:
    result=' '.join(value.split())
    if not 3<=len(result)<=1000:raise DomainError('Reference question is outside its supported length.')
    return result


def _canonical_projection(value:object)->str:
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError) as exc:
        raise DomainError('Projection result is not strict JSON.',409) from exc


def _copy_projection(value:object):
    return json.loads(_canonical_projection(value))


def _projection_sha256(value:object)->str:
    return hashlib.sha256(_canonical_projection(value).encode('utf-8')).hexdigest()


def _projection_semantic(value:object):
    """Only receipt cache observation is excluded from a projection identity."""
    if isinstance(value,dict):
        return {key:_projection_semantic_receipts(child) if key=='execution_receipts'
                else _copy_projection(child) for key,child in value.items()}
    if isinstance(value,list):return [_copy_projection(child) for child in value]
    return _copy_projection(value)


def _projection_semantic_receipts(value:object):
    if not isinstance(value,list):return _copy_projection(value)
    return [{key:_copy_projection(child) for key,child in item.items() if key!='cached'}
            if isinstance(item,dict) else _copy_projection(item) for item in value]


def _projection_looking(value:object)->bool:
    if not isinstance(value,dict):return False
    if _PROJECTION_MARKERS & set(value):return True
    if (value.get('feature')=='APPEND_ONLY_PROJECTION_LOOP'
            or value.get('selector_version')=='literal-page-selector-10'):
        return True
    receipts=value.get('execution_receipts')
    return isinstance(receipts,list) and any(
        isinstance(item,dict) and item.get('receipt_version') in {
            'reference-model-input-receipt-4','reference-model-input-receipt-5',
            'reference-model-input-receipt-6'} for item in receipts)


def require_legacy_result(row:dict)->dict:
    """Reject projection or unknown result rows before legacy consumers parse them."""
    if not isinstance(row,dict):raise DomainError('Reference result row is malformed.',409)
    try:result=json.loads(row['result_json'])
    except (KeyError,TypeError,ValueError,json.JSONDecodeError) as exc:
        raise DomainError('Saved Reference result is malformed.',409) from exc
    if row.get('result_kind')!=_LEGACY_RESULT_KIND or _projection_looking(result):
        raise DomainError('Reference result kind and payload are inconsistent.',409)
    return result


def _reject_private_reasoning(value)->None:
    if isinstance(value,dict):
        if any(str(key).casefold() in _FORBIDDEN_KEYS for key in value):
            raise DomainError('Reference results cannot persist private model reasoning.')
        for child in value.values():_reject_private_reasoning(child)
    elif isinstance(value,list):
        for child in value:_reject_private_reasoning(child)


def _semantic_result(value):
    """Remove request-instance telemetry from the immutable result identity."""
    if isinstance(value,dict):
        result={}
        for key,child in value.items():
            if key in ('model_called','model_call_count','cached'):continue
            if key=='question' and isinstance(child,str):result[key]=_normalized_question(child)
            else:result[key]=_semantic_result(child)
        return result
    if isinstance(value,list):return [_semantic_result(child) for child in value]
    return value


def _portable_citation(value:dict)->dict:
    """Keep human-readable source content while removing run-local identities."""
    omitted={'evidence_id','document_id','region_id','source_evidence_ids'}
    return {key:deepcopy(child) for key,child in value.items() if key not in omitted}


def _portable_value(value):
    omitted={
        'run_id','snapshot_id','evidence_id','evidence_ids','document_id','region_id',
        'source_evidence_ids','selection_id','page_key','model_called','model_call_count','cached',
    }
    if isinstance(value,dict):
        return {key:_portable_value(child) for key,child in value.items() if key not in omitted}
    if isinstance(value,list):return [_portable_value(child) for child in value]
    return value


def _outcome_payload(result:dict,citations:list[dict])->dict:
    by_claim={}
    for item in citations:
        by_claim.setdefault(item['claim_index'],[]).append(_portable_citation(item['citation']))
    claims=[]
    for index,claim in enumerate(result.get('claims',[])):
        claims.append({'text':claim.get('text',''),'citations':by_claim.get(index,[])})
    return {
        'status':result.get('status'),'answer':result.get('answer',''),
        'claims':claims,'missing':deepcopy(result.get('missing',[])),
        'calculations':_portable_value(deepcopy(result.get('calculations',[]))),
    }


class ReferenceResultStore:
    def __init__(self,db:Database,uploads=None):
        self.db=db
        self.uploads=uploads

    def _projection_run(self,row:dict)->dict:
        run=self.db.one('SELECT * FROM runs WHERE id=?',(row['run_id'],),False)
        if (run is None or run.get('project_id')!=row['project_id']
                or run.get('snapshot_id')!=row['snapshot_id']):
            raise DomainError('Projection result run identity is inconsistent.',409)
        return run

    @staticmethod
    def _projection_record(row:dict,*,require_hash:bool=False)->dict:
        try:record=json.loads(row['result_json'])
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise DomainError('Projection result is malformed.',409) from exc
        if (require_hash and (not isinstance(row.get('result_json'),str)
                or hashlib.sha256(row['result_json'].encode('utf-8')).hexdigest()!=row.get('result_hash'))):
            raise DomainError('Projection result hash is inconsistent.',409)
        return record

    def _authenticate_projection_row(self,row:dict)->dict:
        return self._authenticate_projection_row_stage(row)[0]

    def _authenticate_projection_row_stage(self,row:dict)->tuple:
        if row.get('result_kind')!=PROJECTION_RESULT_KIND:
            raise DomainError('Projection result kind is inconsistent.',409)
        if not isinstance(row.get('id'),str) or not _RESULT_ID.fullmatch(row['id']):
            raise DomainError('Projection result identifier is inconsistent.',409)
        record=self._projection_record(row,require_hash=True)
        if not _projection_looking(record):
            raise DomainError('Projection result payload is inconsistent.',409)
        if self.uploads is None:
            raise DomainError('Projection result source authentication requires local uploads.',409)
        run=self._projection_run(row);route=record.get('execution_profile')
        try:authenticated,stage=_authenticate_projection_execution_stage(
            self.db,self.uploads,run,row['question'],route,record)
        except ProjectionDecisionError as exc:
            raise DomainError('Projection result authentication failed.',409) from exc
        receipts=authenticated['execution_receipts'];provider=authenticated['execution_profile']['provider']
        model=receipts[-1]['model'];normalized=_normalized_question(row['question'])
        expected_key=hashlib.sha256(_canonical_projection([
            _PROJECTION_KEY_DOMAIN,run['id'],run['snapshot_id'],
            hashlib.sha256(normalized.encode('utf-8')).hexdigest(),provider,model,
            _projection_sha256(_projection_semantic(authenticated)),
        ]).encode('utf-8')).hexdigest()
        citation_count=self.db.one('SELECT COUNT(*) AS n FROM reference_result_citations WHERE result_id=?',
                                   (row['id'],))['n']
        review_event_count=self.db.one('SELECT COUNT(*) AS n FROM reference_result_review_events WHERE result_id=?',
                                      (row['id'],))['n']
        if (row.get('result_key')!=expected_key or row.get('project_id')!=run['project_id']
                or row.get('run_id')!=run['id'] or row.get('snapshot_id')!=run['snapshot_id']
                or row.get('qa_version')!='3' or row.get('question')!=authenticated['question']
                or row.get('question_key')!=authenticated['question_key']
                or row.get('status')!=authenticated['status']
                or row.get('answer_basis')!=authenticated['answer_basis']
                or row.get('provider')!=provider or row.get('model')!=model
                or row.get('review_status')!='NOT_APPLICABLE' or row.get('review_version')!=0
                or row.get('review_event_id') is not None or citation_count!=0 or review_event_count!=0):
            raise DomainError('Projection result metadata is inconsistent.',409)
        return authenticated,stage

    def _authenticated_projection_review(self,result_id:str)->tuple:
        """One full source/ledger/row check, before deriving any source text or geometry."""
        row=self.db.one('SELECT * FROM reference_results WHERE id=?',(result_id,))
        try:record,stage=self._authenticate_projection_row_stage(row)
        except DomainError as exc:
            raise DomainError('Projection review source authentication failed.',409) from exc
        if (record['projection_execution_version']!='reference-projection-execution-2'
                or record['status']!='REVIEW_REQUIRED'):
            raise DomainError('This result has no supported projection review sources.',409)
        documents={}
        refs=stage.rebuild_refs
        for binding in record['review_packet']['source_bindings']:
            document_id=binding['document_id']
            if document_id in documents:continue
            document=self.db.one('SELECT * FROM documents WHERE id=?',(document_id,),False)
            source_refs=[ref for ref in refs if ref['document_id']==document_id]
            if (document is None or document['project_id']!=stage.project_id
                    or not source_refs or not isinstance(document.get('name'),str)
                    or not document['name']
                    or any(document['sha256']!=ref['pdf_sha256']
                           or document['object_key']!=ref['object_key'] for ref in source_refs)):
                raise DomainError('Projection review document identity changed.',409)
            documents[document_id]=document
        view=_build_review_view_from_authenticated_stage(
            record,stage,result_id=row['id'],result_hash=row['result_hash'],
            document_names={key:document['name'] for key,document in documents.items()})
        try:validate_schema('reference-projection-review-view',view)
        except ValidationError as exc:
            raise DomainError('Projection review view is malformed.',409) from exc
        return view,documents

    def get_projection_review_view(self,result_id:str)->dict:
        return self._authenticated_projection_review(result_id)[0]

    def get_projection_review_image(self,result_id:str,ordinal:int)->bytes:
        if type(ordinal) is not int or ordinal<1:
            raise DomainError('Projection review source does not exist.',404)
        view,documents=self._authenticated_projection_review(result_id)
        sources=[source for source in view['selections'] if source['ordinal']==ordinal]
        if len(sources)!=1:
            raise DomainError('Projection review source does not exist.',404)
        source=sources[0];document=documents[source['document_id']]
        if document['size']>250*1024*1024:
            raise DomainError('Projection image preview is limited to 250 MiB source files.',422)
        try:pdf_bytes=self.uploads.object_path(document).read_bytes()
        except (OSError,DomainError) as exc:
            raise DomainError('Projection review source is unavailable.',409) from exc
        return _render_review_source_from_authenticated_bytes(pdf_bytes,source)[0]

    def _public_projection(self,row:dict,detail:bool)->dict:
        record=self._authenticate_projection_row(row)
        result=_copy_projection(record) if detail else {
            key:_copy_projection(record[key]) for key in (
                'status','reason_code','answer','claims','calculations','missing','answer_basis',
                'execution_receipts','decision_count','supplement_round_count',
                'accepted_supplement_request_count','projection_row_count')}
        value={
            'result_kind':PROJECTION_RESULT_KIND,'result_id':row['id'],'project_id':row['project_id'],
            'run_id':row['run_id'],'snapshot_id':row['snapshot_id'],'qa_version':row['qa_version'],
            'question':row['question'],'status':row['status'],'answer_basis':row['answer_basis'],
            'provider':row['provider'],'model':row['model'],'result_hash':row['result_hash'],
            'result':result,'citations':[],
            'review':{'status':'NOT_APPLICABLE','version':0,'event_id':None},
            'created_at':row['created_at'],'updated_at':row['updated_at'],
        }
        try:validate_schema('reference-projection-result',value)
        except ValidationError as exc:
            raise DomainError('Projection result public envelope is malformed.',409) from exc
        return value

    def save_projection(self,run:dict,question:str,runtime_output:dict,route:dict)->dict:
        if not isinstance(runtime_output,dict) or runtime_output.get('status')=='MODEL_DISABLED':
            raise DomainError('MODEL_DISABLED projection output cannot be saved.',409)
        record=compile_projection_execution(run,question,route,runtime_output)
        if self.uploads is None:
            raise DomainError('Projection result source authentication requires local uploads.',409)
        record=authenticate_projection_execution(self.db,self.uploads,run,question,route,record)
        raw=_canonical_projection(record);raw_hash=hashlib.sha256(raw.encode('utf-8')).hexdigest()
        provider=record['execution_profile']['provider'];model=record['execution_receipts'][-1]['model']
        question_key=record['question_key']
        result_key=hashlib.sha256(_canonical_projection([
            _PROJECTION_KEY_DOMAIN,run['id'],run['snapshot_id'],question_key,provider,model,
            _projection_sha256(_projection_semantic(record)),
        ]).encode('utf-8')).hexdigest()
        result_id=uid('QAR');timestamp=now()
        with self.db.connect(True) as connection:
            existing=connection.execute('SELECT id FROM reference_results WHERE result_key=?',(result_key,)).fetchone()
            if existing is not None:result_id=existing['id']
            else:connection.execute('''INSERT INTO reference_results(
                id,result_key,result_kind,project_id,run_id,snapshot_id,qa_version,question,question_key,
                status,answer_basis,provider,model,result_hash,result_json,review_status,
                review_version,review_event_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                    result_id,result_key,PROJECTION_RESULT_KIND,run['project_id'],run['id'],run['snapshot_id'],'3',
                    question,question_key,record['status'],record['answer_basis'],provider,model,raw_hash,raw,
                    'NOT_APPLICABLE',0,None,timestamp,timestamp))
        return self.get(result_id)

    @staticmethod
    def _execution_profile(result:dict)->dict|None:
        """Return the complete-source, non-secret model routing contract."""
        profile=result.get('execution_profile')
        if profile is None:return None
        expected={'provider','text_model','vision_enabled','vision_model'}
        named={'profile_version','profile_id','max_output_tokens'}
        if (not isinstance(profile,dict) or set(profile) not in (expected,expected|named)
                or not all(isinstance(profile[key],str) and profile[key]
                           for key in ('provider','text_model'))
                or type(profile['vision_enabled']) is not bool
                or not (isinstance(profile['vision_model'],str) and profile['vision_model']
                        or (not profile['vision_enabled'] and profile['vision_model'] is None))
                or (set(profile)==expected|named and (
                    profile['profile_version']!='reference-text-profile-1'
                    or profile['profile_id'] not in ('FLASH_NONE','FLASH_LOW','PRO')
                    or profile['max_output_tokens'] not in (2600,8000)))):
            raise DomainError('Reference execution profile is malformed.')
        if set(profile)==expected|named:
            from app.reference_text_profiles import profile as canonical_profile
            canonical=canonical_profile(profile['profile_id'])
            if any(profile[key]!=canonical[key] for key in ('provider','text_model','vision_enabled',
                                                            'vision_model','profile_version','profile_id',
                                                            'max_output_tokens')):
                raise DomainError('Reference execution profile is not a canonical named text profile.')
        return profile

    def _execution_receipts(
            self,run:dict,result:dict,provider:str,model:str|None=None)->list[dict]:
        receipts=result.get('execution_receipts',[])
        if not isinstance(receipts,list) or len(receipts)>3:
            raise DomainError('Reference execution receipts are malformed.')
        profile=self._execution_profile(result)
        is_complete=profile is not None
        if 'profile_id' in (profile or {}) and not receipts:
            raise DomainError(
                'Named Reference text profiles require a settled execution receipt.',409)
        if any(isinstance(item,dict) and item.get('receipt_version') in (
                   'reference-model-input-receipt-2','reference-model-input-receipt-3')
               for item in receipts) and profile is None:
            raise DomainError('Complete-source Reference results require an execution profile.')
        if profile is not None and profile['provider']!=provider:
            raise DomainError('Reference execution profile provider is inconsistent.')
        versions={item.get('receipt_version') for item in receipts if isinstance(item,dict)}
        if len(versions)>1:
            raise DomainError('Reference execution receipts cannot mix input versions.',409)
        if 'reference-model-input-receipt-3' in versions and 'profile_id' not in (profile or {}):
            raise DomainError('Reference layout input requires a canonical named text profile.',409)
        if is_complete and any((item.get('receipt_version'),item.get('selector_version')) not in (
                ('reference-model-input-receipt-2','literal-page-selector-8'),
                ('reference-model-input-receipt-3','literal-page-selector-9'))
                for item in receipts if isinstance(item,dict)):
            raise DomainError('Reference execution profile requires a complete-source input version.')
        sources=None;call_ids=set();rounds=set()
        question_hash=hashlib.sha256(
            _normalized_question(result['question']).encode('utf-8')).hexdigest()
        for receipt in receipts:
            validate_schema('reference-model-input-receipt',receipt)
            call_id=receipt['model_call_id'];round_index=receipt['round']
            if call_id in call_ids or round_index in rounds:
                raise DomainError('Reference execution receipts are duplicated.')
            call_ids.add(call_id);rounds.add(round_index)
            if receipt['provider']!=provider or receipt['question_hash']!=question_hash:
                raise DomainError('Reference execution receipt identity is inconsistent.')
            if is_complete:
                has_images=bool(receipt['visual_inputs'])
                expected_model=(profile['vision_model'] if has_images
                                else profile['text_model'])
                if (receipt['provider']!=profile['provider']
                        or receipt['model']!=expected_model
                        or (has_images and not profile['vision_enabled'])):
                    raise DomainError('Reference execution receipt does not match its execution profile.')
                if 'profile_id' in profile and (
                    receipt['model']!=profile['text_model']
                    or receipt['inference_mode']!=({'FLASH_LOW':'thinking-low'}.get(profile['profile_id'],'disabled'))
                    or receipt['api_protocol']!='chat_completions'
                    or receipt['structured_output_mode']!='json_object'
                    or receipt['max_output_tokens']!=profile['max_output_tokens']
                    or receipt['visual_inputs']!=[]
                    or not isinstance(receipt.get('initial_evidence_manifest_sha256'),str)
                    or not isinstance(receipt.get('profile_neutral_input_sha256'),str)
                    or not _HASH.fullmatch(receipt['profile_neutral_input_sha256'])):
                    raise DomainError('Reference execution receipt does not match its named text profile.')
            call=self.db.one('''SELECT project_id,run_id,model,state,request_hash,response
                                        ,reference_input_commitment_version,
                                        reference_input_commitment_sha256
                                FROM model_calls WHERE id=?''',(call_id,),False)
            if (call is None or call['project_id']!=run['project_id']
                    or call['run_id']!=run['id'] or call['model']!=receipt['model']
                    or call['state']!='SETTLED'
                    or call['request_hash']!=receipt['request_hash']):
                raise DomainError('Reference execution receipt does not match its settled call.')
            from app.reference_input_commitment import require_ledger_profile
            require_ledger_profile(call,(profile or {}).get('profile_id'))
            if 'profile_id' in (profile or {}):
                from app.reference_input_commitment import VERSION,sha256
                from app.reference_text_profiles import profile as canonical_profile
                if (call['reference_input_commitment_version']!=VERSION
                        or call['reference_input_commitment_sha256']!=sha256(
                            canonical_profile(profile['profile_id']),receipt)):
                    raise DomainError(
                        'Reference execution receipt does not match its settled input commitment.',409)
                if call['response'] is None:
                    raise DomainError(
                        'Named Reference execution receipt has no settled provider response.',409)
            if sources is None:sources=self._source_evidence(run)
            if any(item['evidence_id'] not in sources for item in receipt['evidence_inputs']):
                raise DomainError('Reference execution receipt names evidence outside its snapshot.')
            if receipt['receipt_version']=='reference-model-input-receipt-3':
                from app.reference_layout_input import authenticate_receipt_sources
                authenticate_receipt_sources(receipt,sources)
            if receipt['receipt_version']=='reference-model-input-receipt-2':
                if (receipt['evidence_count']!=len(receipt['evidence_inputs'])
                        or len({item['evidence_id'] for item in receipt['evidence_inputs']})
                        !=len(receipt['evidence_inputs'])
                        or receipt['image_bytes']!=sum(item['image_bytes'] for item in receipt['visual_inputs'])):
                    raise DomainError('Reference execution receipt input counts are inconsistent.')
                for item in receipt['evidence_inputs']:
                    source=sources[item['evidence_id']]
                    if item['text_sha256']!=hashlib.sha256(source['raw_text'].encode('utf-8')).hexdigest():
                        raise DomainError('Reference execution receipt evidence hash is inconsistent.')
            if receipt['receipt_version']=='reference-model-input-receipt-2':
                # A multi-round result keeps final-round regions for claim
                # citation authority, plus a separate complete sent-region
                # audit trail for receipt reauthentication.  Older one-round
                # results did not need the separate field, so retain a
                # read-compatible fallback rather than weakening hash checks.
                audit_regions=result.get('sent_visual_regions',result.get('visual_regions',[]))
                regions={item.get('region_id'):item for item in audit_regions if isinstance(item,dict)}
                for image in receipt['visual_inputs']:
                    region=regions.get(image['region_id'])
                    if region is None or image['image_sha256']!=region.get('image_sha256'):
                        raise DomainError('Reference execution receipt visual input is inconsistent.')
                    region_sources=region.get('source_evidence_ids')
                    if (not isinstance(region_sources,list) or not region_sources
                            or any(not isinstance(source_id,str) or source_id not in sources
                                   or sources[source_id]['document_id']!=region.get('document_id')
                                   or (sources[source_id].get('locator') or {}).get('page_number')!=region.get('page_number')
                                   for source_id in region_sources)):
                        raise DomainError('Reference execution receipt visual source is inconsistent.')
                inputs=receipt['evidence_inputs']
                if (receipt['ordered_evidence_manifest_sha256']!=hashlib.sha256(dumps(inputs).encode()).hexdigest()
                        or receipt['source_text_bytes']!=sum(len(sources[item['evidence_id']]['raw_text'].encode('utf-8')) for item in inputs)):
                    raise DomainError('Reference execution receipt complete-source manifest is inconsistent.')
        if rounds and sorted(rounds)!=list(range(1,max(rounds)+1)):
            raise DomainError('Reference execution receipt rounds are not contiguous.')
        if is_complete and [receipt['round'] for receipt in receipts]!=list(range(1,len(receipts)+1)):
            raise DomainError('Reference execution receipt rounds are not ordered.')
        if 'profile_id' in (profile or {}):
            initials=[receipt.get('initial_evidence_manifest_sha256') for receipt in receipts]
            if (not initials or any(not isinstance(value,str) or not _HASH.fullmatch(value)
                                   for value in initials) or len(set(initials))!=1):
                raise DomainError(
                    'Named Reference execution receipts require one shared initial evidence manifest.',409)
        if is_complete and receipts and model is not None and receipts[-1]['model']!=model:
            raise DomainError('Reference result terminal model is inconsistent.')
        return receipts

    def authenticate_saved_result(self,run:dict,row:dict)->tuple[dict,list[dict],dict[str,dict]]:
        """Revalidate immutable saved output before another workflow relies on its receipt."""
        result=require_legacy_result(row)
        if (hashlib.sha256(row['result_json'].encode('utf-8')).hexdigest()!=row['result_hash']
                or row['project_id']!=run['project_id'] or row['run_id']!=run['id']
                or row['snapshot_id']!=run['snapshot_id'] or result.get('run_id')!=run['id']
                or result.get('source_scope',{}).get('snapshot_id')!=run['snapshot_id']
                or result.get('question')!=row['question'] or result.get('status')!=row['status']):
            raise DomainError('Saved Reference result identity is inconsistent.',409)
        try:receipts=self._execution_receipts(run,result,row['provider'],row['model'])
        except DomainError as exc:
            # These guards emit fixed application messages, not provider or
            # schema bodies. Preserve the useful historical failure category.
            raise DomainError(str(exc),409) from exc
        except ValidationError as exc:
            raise DomainError('Saved Reference execution receipt cannot be authenticated.',409) from exc
        if not receipts:raise DomainError('Saved Reference result has no model execution receipt.',409)
        return result,receipts,self._source_evidence(run)

    def _source_evidence(self,run:dict)->dict[str,dict]:
        document_ids=run.get('document_ids')
        if isinstance(document_ids,str):
            try:document_ids=json.loads(document_ids)
            except (TypeError,ValueError,json.JSONDecodeError):document_ids=None
        if (not isinstance(document_ids,list) or not document_ids
                or any(not isinstance(value,str) or not value for value in document_ids)
                or len(set(document_ids))!=len(document_ids)):
            raise DomainError('Saved Reference QA run documents are malformed.',409)
        run_documents=set(document_ids)
        rows=self.db.all('''SELECT e.payload,e.document_id,d.name AS file_name,
                                   d.project_id AS document_project_id FROM evidence e
                            JOIN documents d ON d.id=e.document_id
                            WHERE e.run_id=? AND e.project_id=?''',(run['id'],run['project_id']))
        result={}
        for row in rows:
            try:value=json.loads(row['payload'])
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                raise DomainError('Saved Reference QA evidence is malformed.',409) from exc
            evidence_id=value.get('evidence_id')
            if (not isinstance(evidence_id,str) or not evidence_id
                    or value.get('project_id')!=run['project_id']
                    or value.get('input_snapshot_id')!=run['snapshot_id']
                    or row['document_project_id']!=run['project_id']
                    or row['document_id'] not in run_documents
                    or value.get('document_id')!=row['document_id']
                    or evidence_id in result):
                raise DomainError('Saved Reference QA evidence does not match its snapshot.',409)
            result[evidence_id]={**value,'file_name':row['file_name']}
        return result

    def _citations(self,run:dict,result:dict)->list[dict]:
        sources=self._source_evidence(run)
        regions={item.get('region_id'):item for item in result.get('visual_regions',[])
                 if isinstance(item,dict) and isinstance(item.get('region_id'),str)}
        citations=[]
        for claim_index,claim in enumerate(result.get('claims',[])):
            if not isinstance(claim,dict):raise DomainError('Reference result claim is malformed.')
            for citation_index,citation in enumerate(claim.get('citations',[])):
                if not isinstance(citation,dict):raise DomainError('Reference result citation is malformed.')
                if citation.get('type')=='TEXT':
                    source=sources.get(citation.get('evidence_id'));quote=citation.get('quote')
                    if source is None or not isinstance(quote,str) or quote not in source.get('raw_text',''):
                        raise DomainError('Reference result text citation is outside the saved evidence.')
                    locator=source.get('locator') if isinstance(source.get('locator'),dict) else {}
                    canonical={
                        'type':'TEXT','evidence_id':source['evidence_id'],'quote':quote,
                        'document_id':source['document_id'],'file_name':source['file_name'],
                        'locator':locator,
                    }
                    evidence_id=source['evidence_id'];region_id=None
                    page=locator.get('page_number') if type(locator.get('page_number')) is int else None
                elif citation.get('type')=='IMAGE_REGION':
                    region=regions.get(citation.get('region_id'))
                    if (region is None or citation.get('document_id')!=region.get('document_id')
                            or citation.get('page_number')!=region.get('page_number')
                            or citation.get('bbox')!=region.get('bbox')
                            or citation.get('needs_review') is not True
                            or not isinstance(citation.get('observation'),str)):
                        raise DomainError('Reference result image citation is outside the supplied page regions.')
                    image_hash=region.get('image_sha256')
                    if not isinstance(image_hash,str) or not _HASH.fullmatch(image_hash):
                        raise DomainError('Reference result image citation has no valid sent-image hash.')
                    canonical={
                        'type':'IMAGE_REGION','region_id':region['region_id'],
                        'document_id':region['document_id'],'file_name':region['file_name'],
                        'page_number':region['page_number'],'bbox':region['bbox'],
                        'coordinate_system':region['coordinate_system'],
                        'observation':citation['observation'],'needs_review':True,
                        'image_sha256':image_hash,
                    }
                    evidence_id=None;region_id=region['region_id'];page=region['page_number']
                else:raise DomainError('Reference result citation type is unsupported.')
                citations.append({
                    'ordinal':len(citations),'claim_index':claim_index,
                    'citation_index':citation_index,'citation_type':canonical['type'],
                    'evidence_id':evidence_id,'region_id':region_id,
                    'document_id':canonical['document_id'],'page_number':page,
                    'citation':canonical,
                })
                if len(citations)>MAX_RESULT_CITATIONS:
                    raise DomainError('Reference result has too many citations.')
        return citations

    def save(self,run:dict,question:str,result:dict,provider:str,model:str)->dict:
        normalized=_normalized_question(question)
        if (not isinstance(result,dict) or result.get('qa_version')!='3'
                or result.get('feature')!='BOUNDED_EVIDENCE_LOOP'
                or result.get('run_id')!=run['id'] or result.get('question')!=question
                or result.get('source_scope',{}).get('snapshot_id')!=run['snapshot_id']):
            raise DomainError('Reference result does not match the saved run and question.')
        status=result.get('status')
        if status not in ('ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT','MODEL_DISABLED'):
            raise DomainError('Reference result has an unsupported terminal status.')
        if status=='ANSWERED' and (not result.get('answer') or not result.get('claims')):
            raise DomainError('Answered Reference QA result has no cited claims.')
        if status!='ANSWERED' and result.get('claims'):
            raise DomainError('A non-answer Reference QA result cannot publish claims.')
        if not isinstance(provider,str) or not provider or len(provider)>180:
            raise DomainError('Reference result provider is invalid.')
        if not isinstance(model,str) or not model or len(model)>180:
            raise DomainError('Reference result model is invalid.')
        profile=self._execution_profile(result)
        receipts=self._execution_receipts(run,result,provider)
        stored_model=receipts[-1]['model'] if profile is not None and receipts else model
        _reject_private_reasoning(result)
        raw=dumps(result)
        complete=bool(receipts) and all(
            item['receipt_version'] in ('reference-model-input-receipt-2',
                                       'reference-model-input-receipt-3') for item in receipts)
        # Complete-source receipts and local page manifests grow with source
        # rows, not model output. Do not lose a settled paid result merely
        # because its validated audit trail exceeds the legacy storage bound.
        bounded=result if not complete else {
            key:value for key,value in result.items() if key not in ('execution_receipts','page_selections')}
        if len(dumps(bounded).encode('utf-8'))>MAX_RESULT_BYTES:
            raise DomainError('Reference result exceeds the local persistence bound.')
        citations=self._citations(run,result)
        result_hash=hashlib.sha256(raw.encode('utf-8')).hexdigest()
        question_key=hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        semantic_hash=hashlib.sha256(dumps(_semantic_result(result)).encode('utf-8')).hexdigest()
        result_key=hashlib.sha256(dumps([
            run['id'],run['snapshot_id'],question_key,provider,stored_model,semantic_hash]).encode()).hexdigest()
        existing=self.db.one('SELECT id FROM reference_results WHERE result_key=?',(result_key,),False)
        if existing:return self.get(existing['id'])
        result_id=uid('QAR');timestamp=now()
        review_status='PENDING' if status=='ANSWERED' else 'NOT_APPLICABLE'
        with self.db.connect(True) as connection:
            connection.execute('''INSERT INTO reference_results(
                id,result_key,project_id,run_id,snapshot_id,qa_version,question,question_key,
                status,answer_basis,provider,model,result_hash,result_json,review_status,
                review_version,review_event_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                result_id,result_key,run['project_id'],run['id'],run['snapshot_id'],'3',question,
                question_key,status,result['answer_basis'],provider,stored_model,result_hash,raw,
                review_status,0,None,timestamp,timestamp))
            connection.executemany('''INSERT INTO reference_result_citations(
                result_id,ordinal,claim_index,citation_index,citation_type,evidence_id,region_id,
                document_id,page_number,citation_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',[
                (result_id,item['ordinal'],item['claim_index'],item['citation_index'],
                 item['citation_type'],item['evidence_id'],item['region_id'],item['document_id'],
                 item['page_number'],dumps(item['citation'])) for item in citations])
        return self.get(result_id)

    @staticmethod
    def _summary(result:dict)->dict:
        return {key:deepcopy(result[key]) for key in (
            'status','answer','claims','missing','calculations','answer_basis','model_called',
            'model_call_count','retrieved_count','execution_receipts') if key in result}

    def _public(self,row:dict,detail:bool,connection=None)->dict:
        if row.get('result_kind')==PROJECTION_RESULT_KIND:
            return self._public_projection(row,detail)
        result=require_legacy_result(row)
        citations=[]
        citation_rows=(connection.execute('''SELECT ordinal,claim_index,citation_index,citation_json
                                             FROM reference_result_citations WHERE result_id=?
                                             ORDER BY ordinal''',(row['id'],)).fetchall()
                       if connection is not None else self.db.all(
                           '''SELECT ordinal,claim_index,citation_index,citation_json
                              FROM reference_result_citations WHERE result_id=?
                              ORDER BY ordinal''',(row['id'],)))
        for item in citation_rows:
            citations.append({'ordinal':item['ordinal'],'claim_index':item['claim_index'],
                              'citation_index':item['citation_index'],
                              'citation':json.loads(item['citation_json'])})
        value={
            'result_id':row['id'],'project_id':row['project_id'],'run_id':row['run_id'],
            'snapshot_id':row['snapshot_id'],'qa_version':row['qa_version'],
            'question':row['question'],'status':row['status'],'answer_basis':row['answer_basis'],
            'provider':row['provider'],'model':row['model'],'result_hash':row['result_hash'],
            'result':result if detail else self._summary(result),'citations':citations,
            'review':{'status':row['review_status'],'version':row['review_version'],
                      'event_id':row['review_event_id']},
            'created_at':row['created_at'],'updated_at':row['updated_at'],
        }
        validate_schema('reference-result',value)
        return value

    def get(self,result_id:str,detail:bool=True)->dict:
        row=self.db.one('SELECT * FROM reference_results WHERE id=?',(result_id,))
        return self._public(row,detail)

    @staticmethod
    def _analysis_mode(run:dict)->str:
        try:capabilities=json.loads(run['capabilities'])
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise DomainError('The saved run has invalid capabilities.',409) from exc
        return capabilities.get('analysis_mode','LEGACY_ANALYSIS')

    def _reference_run(self,project_id:str,run_id:str)->dict:
        run=self.db.one('''SELECT id,project_id,snapshot_id,status,created_at,capabilities
                           FROM runs WHERE id=?''',(run_id,))
        if run['project_id']!=project_id:
            raise DomainError('Reference result run is outside this project.',404)
        if self._analysis_mode(run)!='REFERENCE_QA':
            raise DomainError('Reference result operations require a REFERENCE_QA run.',409)
        return run

    def list(self,project_id:str,run_id:str|None=None,offset:int=0,limit:int=20)->dict:
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        where='project_id=?';args:list=[project_id]
        if run_id is not None:
            run=self.db.one('SELECT project_id FROM runs WHERE id=?',(run_id,))
            if run['project_id']!=project_id:raise DomainError('Reference result run is outside this project.',404)
            where+=' AND run_id=?';args.append(run_id)
        total=self.db.one(f'SELECT COUNT(*) AS n FROM reference_results WHERE {where}',tuple(args))['n']
        rows=self.db.all(f'''SELECT * FROM reference_results WHERE {where}
                            ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?''',
                         (*args,limit,offset))
        return {'items':[self._public(row,False) for row in rows],
                'total':total,'offset':offset,'limit':limit}

    def export(self,project_id:str,run_id:str|None=None,
               review_status:str|None=None)->dict:
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            project_row=connection.execute('SELECT id,name FROM projects WHERE id=?',(project_id,)).fetchone()
            if project_row is None:raise DomainError('Project was not found.',404)
            project=dict(project_row);where='project_id=?';args:list=[project_id]
            if run_id is not None:
                run_row=connection.execute('''SELECT id,project_id,snapshot_id,status,created_at,capabilities
                                              FROM runs WHERE id=?''',(run_id,)).fetchone()
                if run_row is None or run_row['project_id']!=project_id:
                    raise DomainError('Reference result run is outside this project.',404)
                if self._analysis_mode(dict(run_row))!='REFERENCE_QA':
                    raise DomainError('Reference result operations require a REFERENCE_QA run.',409)
                where+=' AND run_id=?';args.append(run_id)
            if review_status is not None:
                if review_status not in ('PENDING','ACCEPTED','REJECTED','NOT_APPLICABLE'):
                    raise DomainError('Reference export review status is invalid.')
                where+=' AND review_status=?';args.append(review_status)
            count=connection.execute(
                f'SELECT COUNT(*) AS n FROM reference_results WHERE {where}',tuple(args)).fetchone()['n']
            if count>MAX_EXPORT_RESULTS:
                raise DomainError(
                    f'Reference export exceeds the {MAX_EXPORT_RESULTS}-result bound; filter by run or review status.',409)
            rows=connection.execute(f'''SELECT * FROM reference_results WHERE {where}
                                        ORDER BY created_at,rowid''',tuple(args)).fetchall()
            items=[];used=0
            for raw_row in rows:
                row=dict(raw_row);result=self._public(row,True,connection)
                result['review_history']=self._history(row['id'],connection)
                used+=len(dumps(result).encode('utf-8'))
                if used>MAX_EXPORT_BYTES:
                    raise DomainError('Reference export exceeds the 64 MiB output bound; filter by run.',409)
                items.append(result)
        value={
            'export_version':'reference-results-json-1','exported_at':now(),
            'project':project,'filters':{'run_id':run_id,'review_status':review_status},
            'result_count':len(items),'results':items,
        }
        if len(dumps(value).encode('utf-8'))>MAX_EXPORT_BYTES:
            raise DomainError('Reference export exceeds the 64 MiB output bound; filter by run.',409)
        return value

    def _comparison_version(self,row:dict)->dict:
        public=self._public(row,True);outcome=_outcome_payload(
            public['result'],public['citations'])
        outcome_hash=hashlib.sha256(dumps(outcome).encode('utf-8')).hexdigest()
        return {
            'result_id':public['result_id'],'status':public['status'],
            'answer_basis':public['answer_basis'],'answer':public['result'].get('answer',''),
            'missing':deepcopy(public['result'].get('missing',[])),
            'calculations':_portable_value(deepcopy(public['result'].get('calculations',[]))),
            'citations':[_portable_citation(item['citation']) for item in public['citations']],
            'result_hash':public['result_hash'],'outcome_hash':outcome_hash,
            'provider':public['provider'],'model':public['model'],
            'review':public['review'],'created_at':public['created_at'],
        }

    def compare(self,project_id:str,baseline_run_id:str,candidate_run_id:str)->dict:
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        if baseline_run_id==candidate_run_id:
            raise DomainError('Reference comparison requires two distinct runs.')
        baseline=self._reference_run(project_id,baseline_run_id)
        candidate=self._reference_run(project_id,candidate_run_id)
        if baseline['status'] not in ('PARTIAL','COMPLETED') or candidate['status'] not in ('PARTIAL','COMPLETED'):
            raise DomainError('Reference comparison requires two terminal saved runs.',409)
        rows=self.db.all('''SELECT * FROM reference_results
                            WHERE project_id=? AND run_id IN (?,?)
                            ORDER BY created_at,rowid''',
                         (project_id,baseline_run_id,candidate_run_id))
        if any(row.get('result_kind')==PROJECTION_RESULT_KIND
               or _projection_looking(self._projection_record(row)) for row in rows):
            raise DomainError('Reference comparison does not support projection outcomes.',409)
        if len(rows)>MAX_COMPARISON_RESULTS:
            raise DomainError('Reference comparison exceeds the 2,000-result bound.',409)
        groups={};used=0
        for row in rows:
            group=groups.setdefault(row['question_key'],{
                'question':_normalized_question(row['question']),'baseline':[],'candidate':[]})
            side='baseline' if row['run_id']==baseline_run_id else 'candidate'
            version=self._comparison_version(row);used+=len(dumps(version).encode('utf-8'))
            if used>MAX_COMPARISON_BYTES:
                raise DomainError('Reference comparison exceeds the 32 MiB output bound.',409)
            group[side].append(version)
        if len(groups)>MAX_COMPARISON_QUESTIONS:
            raise DomainError('Reference comparison exceeds the 1,000-question bound.',409)
        items=[];summary={key:0 for key in ('ADDED','REMOVED','CHANGED','UNCHANGED')}
        for question_key,group in sorted(groups.items(),key=lambda item:(
                item[1]['question'].casefold(),item[0])):
            before=group['baseline'];after=group['candidate']
            if not before:change='ADDED'
            elif not after:change='REMOVED'
            elif sorted(item['outcome_hash'] for item in before)==sorted(
                    item['outcome_hash'] for item in after):change='UNCHANGED'
            else:change='CHANGED'
            summary[change]+=1
            review_changed=bool(before and after and
                                sorted(item['review']['status'] for item in before)!=
                                sorted(item['review']['status'] for item in after))
            processing_changed=bool(before and after and
                                    sorted((item['provider'],item['model'],item['answer_basis'])
                                           for item in before)!=
                                    sorted((item['provider'],item['model'],item['answer_basis'])
                                           for item in after))
            items.append({
                'question_key':question_key,'question':group['question'],'change':change,
                'review_changed':review_changed,'processing_changed':processing_changed,
                'baseline_versions':before,'candidate_versions':after,
            })
        summary['TOTAL']=len(items)
        run_public=lambda run,count:{
            'run_id':run['id'],'snapshot_id':run['snapshot_id'],'status':run['status'],
            'created_at':run['created_at'],'result_count':count}
        identity=[
            'reference-comparison-1',project_id,baseline_run_id,candidate_run_id,
            [(item['question_key'],item['change'],
              [(version['result_hash'],version['review']['status'],version['review']['version'],
                version['provider'],version['model'],version['answer_basis'])
               for version in item['baseline_versions']],
              [(version['result_hash'],version['review']['status'],version['review']['version'],
                version['provider'],version['model'],version['answer_basis'])
               for version in item['candidate_versions']]) for item in items],
        ]
        value={
            'comparison_id':'QACMP-'+hashlib.sha256(dumps(identity).encode()).hexdigest()[:32],
            'comparison_version':'reference-comparison-1','project_id':project_id,
            'policy':'SAVED_PUBLIC_OUTCOME_ONLY_NO_PRECEDENCE','model_called':False,
            'baseline':run_public(baseline,sum(len(item['baseline_versions']) for item in items)),
            'candidate':run_public(candidate,sum(len(item['candidate_versions']) for item in items)),
            'summary':summary,'items':items,
        }
        validate_schema('reference-comparison',value)
        if len(dumps(value).encode('utf-8'))>MAX_COMPARISON_BYTES:
            raise DomainError('Reference comparison exceeds the 32 MiB output bound.',409)
        return value

    def review(self,result_id:str,action:str,expected_version:int,note:str='')->dict:
        if action not in ('ACCEPTED','REJECTED'):raise DomainError('Reference review action is invalid.')
        if type(expected_version) is not int or expected_version<0:
            raise DomainError('Reference review version is invalid.')
        if not isinstance(note,str) or len(note)>2000:raise DomainError('Reference review note is too long.')
        with self.db.connect(True) as connection:
            row=connection.execute('SELECT * FROM reference_results WHERE id=?',(result_id,)).fetchone()
            if row is None:raise DomainError('Reference result was not found.',404)
            row=dict(row)
            if row.get('result_kind')==PROJECTION_RESULT_KIND:
                self._authenticate_projection_row(row)
                raise DomainError('Projection outcomes cannot be reviewed.',409)
            require_legacy_result(row)
            if row['status']!='ANSWERED' or row['review_status']=='NOT_APPLICABLE':
                raise DomainError('Only answered Reference QA results can be reviewed.',409)
            if row['review_version']!=expected_version:
                raise DomainError('Reference result changed; refresh before reviewing.',409)
            event_id=uid('QAREVIEW');timestamp=now()
            before={'status':row['review_status'],'version':row['review_version'],
                    'event_id':row['review_event_id']}
            after={'status':action,'version':row['review_version']+1,'event_id':event_id}
            connection.execute('''INSERT INTO reference_result_review_events
                (id,result_id,actor,action,before_json,after_json,note,created_at)
                VALUES(?,?,?,?,?,?,?,?)''',(
                event_id,result_id,'local-engineer',action,dumps(before),dumps(after),note,timestamp))
            connection.execute('''UPDATE reference_results SET review_status=?,review_version=?,
                                  review_event_id=?,updated_at=? WHERE id=?''',
                               (action,after['version'],event_id,timestamp,result_id))
        return self.get(result_id)

    def history(self,result_id:str)->list[dict]:
        row=self.db.one('SELECT * FROM reference_results WHERE id=?',(result_id,))
        if row.get('result_kind')==PROJECTION_RESULT_KIND:
            self._authenticate_projection_row(row)
            return []
        require_legacy_result(row)
        return self._history(result_id)

    def _history(self,result_id:str,connection=None)->list[dict]:
        result=[]
        rows=(connection.execute('''SELECT id,result_id,actor,action,before_json,after_json,
                                    note,created_at FROM reference_result_review_events
                                    WHERE result_id=? ORDER BY created_at,id''',(result_id,)).fetchall()
              if connection is not None else self.db.all(
                  '''SELECT id,result_id,actor,action,before_json,after_json,
                     note,created_at FROM reference_result_review_events
                     WHERE result_id=? ORDER BY created_at,id''',(result_id,)))
        for raw_row in rows:
            row=dict(raw_row)
            before=json.loads(row.pop('before_json'));after=json.loads(row.pop('after_json'))
            result.append({**row,'before':before,'after':after})
        return result
