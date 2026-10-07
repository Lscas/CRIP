"""Frozen, resumable local question sets for the Reference QA workspace."""
from __future__ import annotations

import hashlib
import json
import re

from jsonschema import ValidationError

from app.db import Database,DomainError,dumps,now,uid
from app.page_selector import COMPLETE_SELECTOR_VERSION as SELECTOR_VERSION,normalize_selector_version
from app.reference_results import ReferenceResultStore,require_legacy_result
from app.reference_text_profiles import profile as text_profile,require_channel,route as text_route
from contracts.runtime_rules import validate_schema

MAX_EVALUATION_QUESTIONS=50
MAX_EVALUATIONS_PER_PAGE=20
MAX_EVALUATION_COMPARISON_BYTES=8*1024*1024
_FAILURE_DETAIL={
    'MODEL_OUTPUT_REJECTED':(
        'The model response or local answer contract did not produce a publishable result. '
        'No unvalidated answer was saved and no retry was started.'),
}
_SAFE_VALIDATOR_CATEGORY=re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


def _question(value:str)->str:
    if not isinstance(value,str):raise DomainError('Reference evaluation question is invalid.')
    normalized=' '.join(value.split())
    if not 3<=len(normalized)<=1000:
        raise DomainError('Reference evaluation question is outside its supported length.')
    return normalized


def _question_key(value:str)->str:
    return hashlib.sha256(_question(value).encode('utf-8')).hexdigest()


def _execution_receipt(raw:str|None)->dict|None:
    if raw is None:return None
    try:value=json.loads(raw)
    except (TypeError,ValueError,json.JSONDecodeError) as exc:
        raise DomainError('Saved Reference execution receipt is malformed.',409) from exc
    try:validate_schema('reference-model-input-receipt',value)
    except ValidationError as exc:
        raise DomainError('Saved Reference execution receipt is malformed.',409) from exc
    return value


def _failure_execution(raw:str|None)->dict|None:
    if raw is None:return None
    try:value=json.loads(raw)
    except (TypeError,ValueError,json.JSONDecodeError) as exc:
        raise DomainError('Saved Reference failure execution is malformed.',409) from exc
    required={'failure_execution_version','receipt_scope','execution_receipts',
              'supplement_round_count','accepted_supplement_request_count'}
    if (not isinstance(value,dict) or set(value)!=required
            or value.get('failure_execution_version')!='reference-failure-execution-1'
            or value.get('receipt_scope')!='COMPLETE_CHAIN'
            or not isinstance(value.get('execution_receipts'),list)
            or type(value.get('supplement_round_count')) is not int
            or type(value.get('accepted_supplement_request_count')) is not int):
        raise DomainError('Saved Reference failure execution is malformed.',409)
    return value


def _contains_failure_secret(value)->bool:
    if isinstance(value,dict):
        if any(key in {'question','query','missing_facts','answer','output','raw_text','reasoning'}
               for key in value):return True
        return any(_contains_failure_secret(item) for item in value.values())
    if isinstance(value,list):return any(_contains_failure_secret(item) for item in value)
    return False


class ReferenceEvaluationStore:
    def __init__(self,db:Database,results:ReferenceResultStore):
        self.db=db;self.results=results

    @staticmethod
    def profile(settings)->dict:
        provider=settings.provider
        return {
            'provider':provider,
            'text_model':'mock-no-network' if provider=='mock' else settings.cheap_model,
            'vision_enabled':bool(provider!='mock' and settings.vision_enabled),
            'vision_model':(settings.vision_model
                            if provider!='mock' and settings.vision_enabled else None),
            'inference_mode':settings.inference_mode(),
            'structured_output_mode':settings.structured_output_mode,
            'api_protocol':settings.api_protocol,
        }

    @staticmethod
    def profile_for_id(profile_id:str|None,settings)->dict:
        if profile_id is None:return ReferenceEvaluationStore.profile(settings)
        value=text_profile(profile_id)
        require_channel(settings,value)
        return value

    @staticmethod
    def _profile(value:dict)->dict:
        """Read older frozen profiles as the json_object behavior they actually used."""
        if not isinstance(value,dict):
            raise DomainError('Reference evaluation model profile is invalid.',409)
        normalized=dict(value)
        normalized.setdefault('structured_output_mode','json_object')
        normalized.setdefault('api_protocol','chat_completions')
        expected={
            'provider','text_model','vision_enabled','vision_model','inference_mode',
            'structured_output_mode',
            'api_protocol',
        }
        if text_route(normalized) is not None:return normalized
        if (set(normalized)!=expected
                or normalized['structured_output_mode'] not in ('json_object','json_schema')
                or normalized['api_protocol'] not in ('chat_completions','responses')):
            raise DomainError('Reference evaluation model profile is invalid.',409)
        return normalized

    @staticmethod
    def _mode(run:dict)->str:
        try:capabilities=json.loads(run['capabilities'])
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise DomainError('The saved run has invalid capabilities.',409) from exc
        return capabilities.get('analysis_mode','LEGACY_ANALYSIS')

    def _run(self,project_id:str,run_id:str)->dict:
        run=self.db.one('''SELECT id,project_id,snapshot_id,status,created_at,capabilities,
                                  document_ids
                           FROM runs WHERE id=?''',(run_id,))
        if run['project_id']!=project_id:
            raise DomainError('Reference evaluation run is outside this project.',404)
        if self._mode(run)!='REFERENCE_QA':
            raise DomainError('Reference evaluations require a REFERENCE_QA run.',409)
        if run['status'] not in ('PARTIAL','COMPLETED'):
            raise DomainError('Reference evaluations require a terminal saved run.',409)
        return run

    def create(self,project_id:str,run_id:str,name:str,questions:list[str],profile:dict,
               selector_version:str=SELECTOR_VERSION)->dict:
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        run=self._run(project_id,run_id)
        if not isinstance(name,str) or not 1<=len(name.strip())<=150:
            raise DomainError('Reference evaluation name is outside its supported length.')
        if (not isinstance(questions,list) or not 1<=len(questions)<=MAX_EVALUATION_QUESTIONS
                or any(not isinstance(item,str) for item in questions)):
            raise DomainError('Reference evaluation requires 1 through 50 questions.')
        normalized=[_question(item) for item in questions]
        keys=[_question_key(item) for item in normalized]
        if len(set(keys))!=len(keys):
            raise DomainError('Reference evaluation questions must be unique after whitespace normalization.')
        profile=self._profile(profile)
        selector_version=normalize_selector_version(selector_version)
        if selector_version=='literal-page-selector-9' and text_route(profile) is None:
            raise DomainError('Reference layout evaluation requires a canonical named text profile.',409)
        raw_profile=dumps(profile)
        if len(raw_profile)>4000:raise DomainError('Reference evaluation model profile is too large.')
        question_set_hash=hashlib.sha256(dumps(normalized).encode('utf-8')).hexdigest()
        evaluation_id=uid('QAE');timestamp=now()
        with self.db.connect(True) as connection:
            connection.execute('''INSERT INTO reference_evaluations(
                id,project_id,run_id,snapshot_id,name,question_set_hash,profile_json,
                created_at,updated_at,selector_version)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',(
                evaluation_id,project_id,run_id,run['snapshot_id'],name.strip(),
                question_set_hash,raw_profile,timestamp,timestamp,selector_version))
            connection.executemany('''INSERT INTO reference_evaluation_items(
                id,evaluation_id,ordinal,question,question_key,result_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)''',[
                (uid('QAEITEM'),evaluation_id,index,question,keys[index],None,timestamp,timestamp)
                for index,question in enumerate(normalized)])
        return self.get(evaluation_id)

    def _row(self,evaluation_id:str)->dict:
        return self.db.one('SELECT * FROM reference_evaluations WHERE id=?',(evaluation_id,))

    def _validator_category(self,receipt:dict|None)->str|None:
        if receipt is None:return None
        call=self.db.one('SELECT state,error FROM model_calls WHERE id=?',
                         (receipt['model_call_id'],),False)
        if call is None or call['state']!='SETTLED_ERROR':return None
        try:diagnostic=json.loads(call['error'] or '')
        except (TypeError,ValueError,json.JSONDecodeError):return None
        if not isinstance(diagnostic,dict) or diagnostic.get('kind')!='CONTRACT_ERROR':return None
        value=diagnostic.get('validator')
        return value if isinstance(value,str) and _SAFE_VALIDATOR_CATEGORY.fullmatch(value) else None

    def _public(self,row:dict)->dict:
        items=[]
        summary={key:0 for key in (
            'PENDING','FAILED','ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT','MODEL_DISABLED',
            'REVIEW_PENDING','ACCEPTED','REJECTED','NOT_APPLICABLE')}
        rows=self.db.all('''SELECT i.*,
                                   r.id AS linked_result_id,r.status AS result_status,
                                   r.result_kind AS linked_result_kind,r.result_json AS linked_result_json,
                                   r.answer_basis AS result_answer_basis,
                                   r.provider AS result_provider,r.model AS result_model,
                                   r.result_hash AS linked_result_hash,
                                   r.review_status,r.review_version,r.review_event_id,
                                   r.created_at AS result_created_at,
                                   r.updated_at AS result_updated_at,
                                   f.id AS failure_id,f.code AS failure_code,
                                   f.stage AS failure_stage,f.detail AS failure_detail,
                                   f.execution_receipt_json AS failure_execution_receipt_json,
                                   f.created_at AS failure_created_at
                            FROM reference_evaluation_items i
                            LEFT JOIN reference_results r ON r.id=i.result_id
                            LEFT JOIN reference_evaluation_failures f ON f.item_id=i.id
                            WHERE i.evaluation_id=? ORDER BY i.ordinal,i.id''',(row['id'],))
        for item in rows:
            if item['linked_result_id'] is not None:
                require_legacy_result({'result_kind':item['linked_result_kind'],
                                       'result_json':item['linked_result_json']})
            linked=(None if item['linked_result_id'] is None else {
                'result_id':item['linked_result_id'],'status':item['result_status'],
                'answer_basis':item['result_answer_basis'],'provider':item['result_provider'],
                'model':item['result_model'],'result_hash':item['linked_result_hash'],
                'review':{'status':item['review_status'],'version':item['review_version'],
                          'event_id':item['review_event_id']},
                'created_at':item['result_created_at'],'updated_at':item['result_updated_at'],
            })
            failure=None
            if item['failure_id'] is not None:
                receipt=_execution_receipt(item['failure_execution_receipt_json'])
                failure={
                    'failure_id':item['failure_id'],'code':item['failure_code'],
                    'stage':item['failure_stage'],'detail':item['failure_detail'],
                    'validator_category':self._validator_category(receipt),
                    'execution_receipt':receipt,'created_at':item['failure_created_at'],
                }
            if linked is not None:
                summary[linked['status']]+=1
                review=linked['review']['status']
                summary['REVIEW_PENDING' if review=='PENDING' else review]+=1
            elif failure is not None:summary['FAILED']+=1
            else:summary['PENDING']+=1
            items.append({
                'item_id':item['id'],'ordinal':item['ordinal'],'question':item['question'],
                'question_key':item['question_key'],
                'state':('COMPLETE' if linked else 'FAILED' if failure else 'PENDING'),
                'result':linked,'failure':failure,
                'created_at':item['created_at'],'updated_at':item['updated_at'],
            })
        summary={'TOTAL':len(items),**summary}
        completed=summary['TOTAL']-summary['PENDING']
        status=('READY' if completed==0 else
                'COMPLETE' if summary['PENDING']==0 else 'IN_PROGRESS')
        value={
            'evaluation_id':row['id'],'project_id':row['project_id'],'run_id':row['run_id'],
            'snapshot_id':row['snapshot_id'],'name':row['name'],
            'question_set_hash':row['question_set_hash'],
            'selector_version':normalize_selector_version(row['selector_version']),
            'profile':self._profile(json.loads(row['profile_json'])),
            'status':status,'summary':summary,'items':items,
            'created_at':row['created_at'],'updated_at':row['updated_at'],
        }
        validate_schema('reference-evaluation',value)
        return value

    def get(self,evaluation_id:str)->dict:
        return self._public(self._row(evaluation_id))

    def list(self,project_id:str,offset:int=0,limit:int=10)->dict:
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        total=self.db.one('''SELECT COUNT(*) AS n FROM reference_evaluations
                             WHERE project_id=?''',(project_id,))['n']
        rows=self.db.all('''SELECT * FROM reference_evaluations WHERE project_id=?
                            ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?''',
                         (project_id,min(limit,MAX_EVALUATIONS_PER_PAGE),offset))
        return {'items':[self._public(row) for row in rows],
                'total':total,'offset':offset,'limit':min(limit,MAX_EVALUATIONS_PER_PAGE)}

    @staticmethod
    def _clone_name(name:str,profile:dict)->str:
        suffix=' · '+profile['text_model'][:80]
        return name[:max(1,150-len(suffix))]+suffix

    def clone_for_profile(self,evaluation_id:str,profile:dict)->dict:
        """Freeze the identical run/questions under another active profile; make no call."""
        source=self.get(evaluation_id);profile=self._profile(profile)
        if source['profile']==profile:
            raise DomainError(
                'Switch to a different model profile before cloning this Reference evaluation.',409)
        run=self._run(source['project_id'],source['run_id'])
        if run['snapshot_id']!=source['snapshot_id']:
            raise DomainError('Reference evaluation snapshot no longer matches its saved run.',409)
        cloned=self.create(
            source['project_id'],source['run_id'],
            self._clone_name(source['name'],profile),
            [item['question'] for item in source['items']],profile,
            source['selector_version'])
        if (cloned['snapshot_id']!=source['snapshot_id']
                or cloned['question_set_hash']!=source['question_set_hash']
                or cloned['selector_version']!=source['selector_version']):
            raise DomainError('Cloned Reference evaluation did not preserve its source identity.',409)
        return {
            'source_evaluation_id':source['evaluation_id'],
            'model_called':False,'evaluation':cloned,
        }

    def _comparison_item(self,item:dict)->dict:
        if item['state']=='PENDING':
            return {'kind':'PENDING','outcome_hash':None,'_receipt_chain':[]}
        if item['failure'] is not None:
            failure=item['failure'];outcome={
                'kind':'FAILURE','code':failure['code'],'stage':failure['stage'],
            }
            failure_execution=item.get('_failure_execution')
            receipt=failure.get('execution_receipt')
            receipts=(failure_execution['execution_receipts'] if failure_execution is not None
                      else ([receipt] if isinstance(receipt,dict) else []))
            manifests={value.get('initial_evidence_manifest_sha256') for value in receipts
                       if isinstance(value,dict) and value.get('initial_evidence_manifest_sha256')}
            return {
                **outcome,'detail':failure['detail'],
                'outcome_hash':hashlib.sha256(dumps(outcome).encode()).hexdigest(),
                '_initial_evidence_manifest_sha256':next(iter(manifests)) if len(manifests)==1 else None,
                '_receipt_chain':receipts,
                '_failure_round':receipts[-1].get('round') if receipts else None,
                '_complete_failure_chain':failure_execution is not None,
                '_manifest_count':len(manifests),
            }
        result=self.results.get(item['result']['result_id'])
        public_result=result['result']
        receipts=public_result.get('execution_receipts',[])
        manifests={item.get('initial_evidence_manifest_sha256') for item in receipts
                   if isinstance(item,dict) and item.get('initial_evidence_manifest_sha256')}
        citations=[entry['citation'] for entry in result['citations']]
        outcome={
            'status':result['status'],'answer':public_result.get('answer',''),
            'missing':public_result.get('missing',[]),
            'calculations':public_result.get('calculations',[]),
            'citations':citations,
        }
        return {
            'kind':'RESULT','result_id':result['result_id'],'status':result['status'],
            'answer_basis':result['answer_basis'],'answer':outcome['answer'],
            'missing':outcome['missing'],'calculations':outcome['calculations'],
            'citations':citations,'result_hash':result['result_hash'],
            'outcome_hash':hashlib.sha256(dumps(outcome).encode()).hexdigest(),
            'provider':result['provider'],'model':result['model'],'review':result['review'],
            'initial_evidence_manifest_sha256':(next(iter(manifests)) if len(manifests)==1 else None),
            '_receipt_chain':receipts,
            '_manifest_count':len(manifests),
        }

    def authenticate_item_result(self,evaluation:dict,item:dict,result_id:str)->dict:
        """Authenticate a saved result in the identity of its frozen item."""
        raw=self.db.one('SELECT * FROM reference_results WHERE id=?',(result_id,))
        require_legacy_result(raw)
        run=self._run(evaluation['project_id'],evaluation['run_id'])
        try:stored=json.loads(raw['result_json'])
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise DomainError('Saved Reference result is malformed.',409) from exc
        frozen=(self._profile(evaluation['profile']) if 'profile' in evaluation
                else self._profile(json.loads(evaluation['profile_json'])))
        saved_profile=stored.get('execution_profile')
        if text_route(frozen) is None and isinstance(saved_profile,dict) and 'profile_id' in saved_profile:
            raise DomainError('A legacy Reference evaluation cannot attach a named text-profile result.',409)
        if text_route(frozen) is not None or stored.get('execution_receipts'):
            result,_,_=self.results.authenticate_saved_result(run,raw)
        else:
            result=stored
        if (_question_key(result['question'])!=item['question_key']
                or raw['project_id']!=evaluation['project_id']
                or raw['run_id']!=evaluation['run_id']
                or raw['snapshot_id']!=evaluation['snapshot_id']):
            raise DomainError('Reference result does not match the frozen evaluation item.',409)
        if text_route(frozen) is not None:
            saved_profile=result.get('execution_profile')
            public_keys=('provider','text_model','vision_enabled','vision_model',
                         'profile_version','profile_id','max_output_tokens')
            if (not isinstance(saved_profile,dict)
                    or any(saved_profile.get(key)!=frozen[key] for key in public_keys)):
                raise DomainError('Reference result does not match the frozen named text profile.',409)
            self._require_route_receipts(frozen,result.get('execution_receipts'))
            self._require_selector_receipts(evaluation,result['execution_receipts'])
        return self.results.get(result_id)

    @staticmethod
    def _comparison_evaluation(value:dict)->dict:
        return {
            key:value[key] for key in (
                'evaluation_id','name','status','selector_version','profile','summary','created_at')
        }

    def compare(self,project_id:str,baseline_id:str,candidate_id:str)->dict:
        """Compare two same-snapshot frozen tasks without inferring correctness."""
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        if baseline_id==candidate_id:
            raise DomainError('Reference evaluation comparison requires two distinct tasks.')
        baseline_row=self._row(baseline_id);candidate_row=self._row(candidate_id)
        if baseline_row['project_id']!=project_id or candidate_row['project_id']!=project_id:
            raise DomainError('Reference evaluation comparison is outside this project.',404)
        baseline=self._public(baseline_row);candidate=self._public(candidate_row)
        identity=(baseline['run_id'],baseline['snapshot_id'],baseline['question_set_hash'],
                  baseline['selector_version'])
        if identity!=(candidate['run_id'],candidate['snapshot_id'],
                     candidate['question_set_hash'],candidate['selector_version']):
            raise DomainError(
                'Reference evaluation comparison requires the same run, snapshot, question set, '
                'and page-selector version.',409)
        if len(baseline['items'])!=len(candidate['items']):
            raise DomainError('Reference evaluation question sets do not match.',409)
        named_comparison=('profile_id' in baseline['profile']
                          or 'profile_id' in candidate['profile'])
        items=[];summary={key:0 for key in ('PENDING','CHANGED','UNCHANGED')}
        identity_items=[]
        for before,after in zip(baseline['items'],candidate['items']):
            if (before['ordinal'],before['question_key'],before['question'])!=(
                    after['ordinal'],after['question_key'],after['question']):
                raise DomainError('Reference evaluation question sets do not match.',409)
            before_execution=None;after_execution=None
            if before['result'] is not None:
                self.authenticate_item_result(baseline,before,before['result']['result_id'])
            elif before['failure'] is not None:
                before_execution=self.authenticate_item_failure(baseline,before,baseline['profile'])
            if after['result'] is not None:
                self.authenticate_item_result(candidate,after,after['result']['result_id'])
            elif after['failure'] is not None:
                after_execution=self.authenticate_item_failure(candidate,after,candidate['profile'])
            baseline_value=self._comparison_item({**before,'_failure_execution':before_execution})
            candidate_value=self._comparison_item({**after,'_failure_execution':after_execution})
            before_chain=baseline_value.pop('_receipt_chain')
            after_chain=candidate_value.pop('_receipt_chain')
            before_failure_round=baseline_value.pop('_failure_round',None)
            after_failure_round=candidate_value.pop('_failure_round',None)
            before_complete_failure=baseline_value.pop('_complete_failure_chain',False)
            after_complete_failure=candidate_value.pop('_complete_failure_chain',False)
            before_path=[(receipt.get('round'),receipt.get('profile_neutral_input_sha256'))
                         for receipt in before_chain]
            after_path=[(receipt.get('round'),receipt.get('profile_neutral_input_sha256'))
                        for receipt in after_chain]
            before_manifest_count=baseline_value.pop('_manifest_count',
                1 if baseline_value.get('initial_evidence_manifest_sha256') else 0)
            after_manifest_count=candidate_value.pop('_manifest_count',
                1 if candidate_value.get('initial_evidence_manifest_sha256') else 0)
            before_manifest=baseline_value.pop('_initial_evidence_manifest_sha256',
                                                baseline_value.get('initial_evidence_manifest_sha256'))
            after_manifest=candidate_value.pop('_initial_evidence_manifest_sha256',
                                               candidate_value.get('initial_evidence_manifest_sha256'))
            before_named='profile_id' in baseline['profile']
            after_named='profile_id' in candidate['profile']
            if 'PENDING' in (baseline_value['kind'],candidate_value['kind']):
                input_path_status='PENDING_UNEXECUTED'
                fair_fixed_input=False
            elif before_named and after_named:
                if (before_manifest_count!=1 or after_manifest_count!=1
                        or not before_manifest or not after_manifest):
                    raise DomainError('Named Reference comparison requires one authenticated initial evidence manifest per item.',409)
                if before_manifest!=after_manifest:
                    raise DomainError('Reference evaluation comparison has incompatible initial evidence manifests.',409)
                if ((baseline_value['kind']=='FAILURE' and before_failure_round and before_failure_round>1
                     and not before_complete_failure)
                        or (candidate_value['kind']=='FAILURE' and after_failure_round and after_failure_round>1
                            and not after_complete_failure)):
                    input_path_status='SAME_INITIAL_PATH_UNKNOWN_FAILURE_HISTORY'
                    fair_fixed_input=False
                elif before_path==after_path:
                    input_path_status='FULL_PATH_MATCHED'
                    fair_fixed_input=True
                else:
                    input_path_status='DYNAMIC_PATH_DIVERGED'
                    fair_fixed_input=False
            elif before_named or after_named:
                input_path_status='UNKNOWN_LEGACY'
                fair_fixed_input=False
            else:
                input_path_status='LEGACY_UNVERIFIED'
                fair_fixed_input=False
            if 'PENDING' in (baseline_value['kind'],candidate_value['kind']):
                change='PENDING'
            elif baseline_value['outcome_hash']==candidate_value['outcome_hash']:
                change='UNCHANGED'
            else:change='CHANGED'
            summary[change]+=1
            review_changed=bool(
                baseline_value['kind']=='RESULT' and candidate_value['kind']=='RESULT'
                and baseline_value['review']['status']!=candidate_value['review']['status'])
            items.append({
                'ordinal':before['ordinal'],'question_key':before['question_key'],
                'question':before['question'],'change':change,
                'review_changed':review_changed,
                'fair_fixed_input':fair_fixed_input,
                'input_path_status':input_path_status,
                'baseline':baseline_value,'candidate':candidate_value,
            })
            identity_items.append((
                before['question_key'],change,baseline_value['outcome_hash'],
                candidate_value['outcome_hash'],
                baseline_value.get('review',{}).get('status'),
                candidate_value.get('review',{}).get('status'),
                *(() if not named_comparison else (
                    fair_fixed_input,input_path_status,before_manifest,after_manifest,
                    before_path,after_path)),
            ))
        summary['TOTAL']=len(items)
        comparison_version=('reference-evaluation-comparison-2' if named_comparison
                            else 'reference-evaluation-comparison-1')
        comparison_identity=[
            comparison_version,project_id,baseline_id,candidate_id,identity_items,
        ]
        value={
            'comparison_id':'QAEVCMP-'+hashlib.sha256(
                dumps(comparison_identity).encode()).hexdigest()[:32],
            'comparison_version':comparison_version,
            'project_id':project_id,'run_id':baseline['run_id'],
            'snapshot_id':baseline['snapshot_id'],
            'question_set_hash':baseline['question_set_hash'],
            'selector_version':baseline['selector_version'],
            'policy':'SAVED_OUTCOME_ONLY_NO_CORRECTNESS_OR_PRECEDENCE',
            'model_called':False,'profile_changed':baseline['profile']!=candidate['profile'],
            'baseline':self._comparison_evaluation(baseline),
            'candidate':self._comparison_evaluation(candidate),
            'summary':summary,'items':items,
        }
        validate_schema('reference-evaluation-comparison',value)
        if len(dumps(value).encode('utf-8'))>MAX_EVALUATION_COMPARISON_BYTES:
            raise DomainError('Reference evaluation comparison exceeds the 8 MiB bound.',409)
        return value

    def item_context(self,evaluation_id:str,item_id:str)->dict:
        evaluation=self._row(evaluation_id)
        item=self.db.one('''SELECT * FROM reference_evaluation_items
                            WHERE id=? AND evaluation_id=?''',(item_id,evaluation_id))
        run=self._run(evaluation['project_id'],evaluation['run_id'])
        if run['snapshot_id']!=evaluation['snapshot_id']:
            raise DomainError('Reference evaluation snapshot no longer matches its saved run.',409)
        return {
            'evaluation':evaluation,'item':item,'run':run,
            'selector_version':normalize_selector_version(evaluation['selector_version']),
            'profile':self._profile(json.loads(evaluation['profile_json'])),
            'saved_result':(self.results.get(item['result_id']) if item['result_id'] else None),
            'saved_failure':self.failure(item['id']),
        }

    def failure(self,item_id:str)->dict|None:
        row=self.db.one('''SELECT id,code,stage,detail,execution_receipt_json,failure_execution_json,created_at
                           FROM reference_evaluation_failures WHERE item_id=?''',
                        (item_id,),False)
        if row is None:return None
        receipt=_execution_receipt(row['execution_receipt_json'])
        return {
            'failure_id':row['id'],'code':row['code'],'stage':row['stage'],
            'detail':row['detail'],'validator_category':self._validator_category(receipt),
            'execution_receipt':receipt,'created_at':row['created_at'],
        }

    def failure_execution(self,item_id:str)->dict|None:
        row=self.db.one('SELECT failure_execution_json FROM reference_evaluation_failures WHERE item_id=?',
                        (item_id,),False)
        return _failure_execution(row['failure_execution_json']) if row is not None else None

    def authenticate_item_failure(self,evaluation:dict,item:dict,profile:dict,
                                  failure:dict|None=None)->dict|None:
        """Authenticate a terminal failure and, when present, its complete v9 chain."""
        failure=item.get('failure') if failure is None else failure
        if failure is None:
            return None
        item_id=item.get('item_id',item.get('id'))
        if not isinstance(item_id,str):
            raise DomainError('Reference evaluation failure item identity is invalid.',409)
        execution=self.failure_execution(item_id)
        # Only pre-named legacy profiles may retain a terminal row without a
        # receipt.  Named profiles have always required an authenticated receipt.
        if (failure.get('execution_receipt') is None and execution is None
                and text_route(profile) is None):
            return None
        if (text_route(profile) is not None
                or isinstance(failure.get('execution_receipt'),dict)):
            self._authenticate_route_failure(evaluation,item,profile,failure['execution_receipt'])
        if execution is not None:
            self._authenticate_failure_execution(
                evaluation,item,profile,failure['execution_receipt'],execution)
        return execution

    def fail(self,evaluation_id:str,item_id:str,code:str,
             execution_receipt:dict|None=None,failure_execution:dict|None=None)->tuple[dict,dict]:
        if code not in _FAILURE_DETAIL:
            raise DomainError('Reference evaluation failure code is invalid.')
        context=self.item_context(evaluation_id,item_id);item=context['item']
        if item['result_id']:
            raise DomainError('Reference evaluation item already has a saved result.',409)
        if context['saved_failure'] is not None:
            self.authenticate_item_failure(
                context['evaluation'],item,context['profile'],context['saved_failure'])
            return self.get(evaluation_id),context['saved_failure']
        if text_route(context['profile']) is not None and execution_receipt is None:
            raise DomainError('Frozen Reference text profile requires an authenticated failure receipt.',409)
        if execution_receipt is not None:
            self._authenticate_route_failure(
                context['evaluation'],item,context['profile'],execution_receipt)
        if failure_execution is not None:
            self._authenticate_failure_execution(context['evaluation'],item,context['profile'],
                                                 execution_receipt,failure_execution)
        elif text_route(context['profile']) is not None and execution_receipt is not None:
            # New named-v9 failures must not silently become terminal-only records.
            if context['selector_version']=='literal-page-selector-9':
                raise DomainError('Named v9 failure requires a complete authenticated receipt chain.',409)
        failure_id=uid('QAEFAIL');timestamp=now();detail=_FAILURE_DETAIL[code]
        with self.db.connect(True) as connection:
            connection.execute('''INSERT INTO reference_evaluation_failures(
                id,evaluation_id,item_id,code,stage,detail,created_at,execution_receipt_json,failure_execution_json)
                VALUES(?,?,?,?,?,?,?,?,?)''',(
                failure_id,evaluation_id,item_id,code,'EXECUTION',detail,timestamp,
                None if execution_receipt is None else dumps(execution_receipt),
                None if failure_execution is None else dumps(failure_execution)))
            connection.execute('''UPDATE reference_evaluation_items SET updated_at=?
                                  WHERE id=? AND evaluation_id=?''',
                               (timestamp,item_id,evaluation_id))
            connection.execute('UPDATE reference_evaluations SET updated_at=? WHERE id=?',
                               (timestamp,evaluation_id))
        return self.get(evaluation_id),self.failure(item_id)

    def _authenticate_failure_execution(self,evaluation:dict,item:dict,profile:dict,
                                        terminal:dict|None,value:dict)->None:
        if terminal is None or not isinstance(value,dict):
            raise DomainError('Named v9 failure execution is incomplete.',409)
        if (text_route(profile) is None
                or evaluation.get('selector_version')!='literal-page-selector-9'):
            raise DomainError('Complete failure execution requires a named v9 evaluation.',409)
        required={'failure_execution_version','receipt_scope','execution_receipts',
                  'supplement_round_count','accepted_supplement_request_count'}
        receipts=value.get('execution_receipts')
        if (set(value)!=required or value.get('failure_execution_version')!='reference-failure-execution-1'
                or value.get('receipt_scope')!='COMPLETE_CHAIN' or not isinstance(receipts,list)
                or not 1<=len(receipts)<=3 or type(value.get('supplement_round_count')) is not int
                or value.get('supplement_round_count')!=len(receipts)-1
                or type(value.get('accepted_supplement_request_count')) is not int
                or not 0<=value['accepted_supplement_request_count']<=4
                or dumps(receipts[-1])!=dumps(terminal)):
            raise DomainError('Named v9 failure execution is malformed.',409)
        if _contains_failure_secret(value):
            raise DomainError('Named v9 failure execution contains prohibited data.',409)
        self._require_route_receipts(profile,receipts);self._require_selector_receipts(evaluation,receipts)
        common=('question_hash','selector_version','prompt_contract_hash','initial_evidence_manifest_sha256')
        if any(not isinstance(receipt,dict) or receipt.get('round')!=index+1
               or receipt.get('receipt_version')!='reference-model-input-receipt-3'
               or any(receipt.get(key)!=receipts[0].get(key) for key in common)
               for index,receipt in enumerate(receipts)):
            raise DomainError('Named v9 failure receipt chain is inconsistent.',409)
        if len({receipt.get('model_call_id') for receipt in receipts}) != len(receipts):
            raise DomainError('Named v9 failure receipt chain reuses a model call.',409)
        try:
            for receipt in receipts:
                validate_schema('reference-model-input-receipt',receipt)
        except ValidationError as exc:
            raise DomainError('Named v9 failure receipt chain is malformed.',409) from exc
        self._authenticate_route_failure(evaluation,item,profile,terminal)
        from app.reference_layout_input import authenticate_receipt_sources
        run=self._run(evaluation['project_id'],evaluation['run_id'])
        if run['snapshot_id']!=evaluation['snapshot_id']:
            raise DomainError('Reference failure snapshot is inconsistent.',409)
        sources=self.results._source_evidence(run)
        from app.evidence_loop import _supplement,validate_evidence_decision
        accepted=0;history=[]
        for index,receipt in enumerate(receipts[:-1]):
            authenticate_receipt_sources(receipt,sources)
            call=self.db.one('''SELECT project_id,run_id,state,request_hash,response,model,
                reference_input_commitment_version,reference_input_commitment_sha256 FROM model_calls WHERE id=?''',
                (receipt['model_call_id'],),False)
            if (call is None or call['project_id']!=evaluation['project_id'] or call['run_id']!=evaluation['run_id']
                    or call['state']!='SETTLED' or call['request_hash']!=receipt['request_hash']
                    or call['model']!=receipt['model']):
                raise DomainError('Named v9 prior failure receipt is not settled.',409)
            from app.reference_input_commitment import VERSION,require_ledger_profile,sha256
            require_ledger_profile(call,profile['profile_id'])
            if (call['reference_input_commitment_version']!=VERSION
                    or call['reference_input_commitment_sha256']!=sha256(text_route(profile),receipt)):
                raise DomainError('Named v9 prior failure receipt has no matching input commitment.',409)
            try:response=json.loads(call['response'] or '')
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                raise DomainError('Named v9 prior failure response is malformed.',409) from exc
            rows=[sources[evidence['evidence_id']] for evidence in receipt['evidence_inputs']]
            try:
                decision=validate_evidence_decision(
                    response,rows,item['question'],history,complete_context=True)
            except (TypeError,ValueError,ValidationError) as exc:
                raise DomainError('Named v9 prior receipt does not require valid evidence.',409) from exc
            if decision['status']!='NEED_EVIDENCE':
                raise DomainError('Named v9 prior receipt does not require evidence.',409)
            history.extend(decision['requests'])
            expected,new_count,_,_=_supplement(
                self.db,run,decision['requests'],rows,
                'literal-page-selector-9',item['question'])
            if new_count<=0:
                raise DomainError('Named v9 supplemental request did not add source evidence.',409)
            next_ids=[evidence['evidence_id'] for evidence in receipts[index+1]['evidence_inputs']]
            if [row['evidence_id'] for row in expected]!=next_ids:
                raise DomainError('Named v9 supplemental input does not match its accepted request.',409)
            accepted+=len(decision['requests'])
        if accepted!=value['accepted_supplement_request_count']:
            raise DomainError('Named v9 supplement counter is inconsistent.',409)

    def _authenticate_route_failure(self,evaluation:dict,item:dict,profile:dict,
                                    execution_receipt:dict)->None:
        """Authenticate the sole durable failure receipt for a named item."""
        route=text_route(profile)
        try:validate_schema('reference-model-input-receipt',execution_receipt)
        except ValidationError as exc:
            raise DomainError('Reference failure receipt is missing or malformed.',409) from exc
        if route is not None:
            self._require_route_receipts(profile,[execution_receipt])
        if route is not None:
            self._require_selector_receipts(evaluation,[execution_receipt])
        elif execution_receipt.get('receipt_version')=='reference-model-input-receipt-3':
            raise DomainError('Reference layout failure requires a canonical named text profile.',409)
        call=self.db.one('''SELECT project_id,run_id,model,state,request_hash,error,
                                    reference_input_commitment_version,
                                    reference_input_commitment_sha256
                            FROM model_calls WHERE id=?''',
                         (execution_receipt['model_call_id'],),False)
        if (call is None or call['project_id']!=evaluation['project_id']
                or call['run_id']!=evaluation['run_id']
                or call['model']!=execution_receipt['model']
                or call['state']!='SETTLED_ERROR'
                or call['request_hash']!=execution_receipt['request_hash']
                or (route is not None and execution_receipt['provider']!=profile['provider'])
                or execution_receipt['question_hash']!=item['question_key']):
            raise DomainError('Reference failure receipt does not match its settled model call.',409)
        from app.reference_input_commitment import require_ledger_profile
        require_ledger_profile(call,route['profile_id'] if route is not None else None)
        if route is None:return
        try:diagnostic=json.loads(call['error'] or '')
        except (TypeError,ValueError,json.JSONDecodeError) as exc:
            raise DomainError('Reference failure call diagnostic is malformed.',409) from exc
        if (not isinstance(diagnostic,dict)
                or diagnostic.get('kind')!='CONTRACT_ERROR'
                or diagnostic.get('class')!='PROJECT_EVIDENCE_DECISION'):
            raise DomainError('Reference failure receipt is not a contract-rejected decision.',409)
        from app.reference_input_commitment import VERSION,sha256
        if (call['reference_input_commitment_version']!=VERSION
                or call['reference_input_commitment_sha256']!=sha256(route,execution_receipt)):
            raise DomainError(
                'Reference failure receipt does not match its settled input commitment.',409)
        if execution_receipt['receipt_version']=='reference-model-input-receipt-3':
            from app.reference_layout_input import authenticate_receipt_sources
            run=self._run(evaluation['project_id'],evaluation['run_id'])
            if run['snapshot_id']!=evaluation['snapshot_id']:
                raise DomainError('Reference failure snapshot is inconsistent.',409)
            authenticate_receipt_sources(execution_receipt,self.results._source_evidence(run))

    @staticmethod
    def _require_selector_receipts(evaluation:dict,receipts:list[dict])->None:
        expected={'literal-page-selector-8':'reference-model-input-receipt-2',
                  'literal-page-selector-9':'reference-model-input-receipt-3'}
        selector=evaluation.get('selector_version')
        if selector not in expected or not receipts or any(
                receipt.get('selector_version')!=selector
                or receipt.get('receipt_version')!=expected[selector] for receipt in receipts):
            raise DomainError('Reference receipt does not match its frozen selector version.',409)

    def require_profile(self,evaluation_id:str,settings)->dict|None:
        row=self._row(evaluation_id);saved=self._profile(json.loads(row['profile_json']))
        route=require_channel(settings,saved)
        if route is not None:return route
        if saved!=self.profile(settings):
            raise DomainError(
                'The active model profile differs from this frozen Reference evaluation.',409)
        return None

    @staticmethod
    def _require_route_receipts(profile:dict,receipts:list[dict]|None)->None:
        route=text_route(profile)
        if route is None:return
        if not isinstance(receipts,list) or not receipts:
            raise DomainError('Frozen Reference text profile requires an authenticated execution receipt.',409)
        for receipt in receipts:
            if (not isinstance(receipt,dict) or receipt.get('provider')!='deepseek'
                    or receipt.get('model')!=route['text_model']
                    or receipt.get('inference_mode')!=route['inference_mode']
                    or receipt.get('api_protocol')!='chat_completions'
                    or receipt.get('structured_output_mode')!='json_object'
                    or receipt.get('max_output_tokens')!=route['max_output_tokens']
                    or receipt.get('visual_inputs')!=[]
                    or not isinstance(receipt.get('initial_evidence_manifest_sha256'),str)
                    or not isinstance(receipt.get('profile_neutral_input_sha256'),str)
                    or not re.fullmatch(r'[0-9a-f]{64}',receipt['profile_neutral_input_sha256'])):
                raise DomainError('Reference execution receipt does not match its frozen text profile.',409)

    def attach(self,evaluation_id:str,item_id:str,result_id:str)->dict:
        context=self.item_context(evaluation_id,item_id)
        item=context['item']
        if context['saved_failure'] is not None:
            raise DomainError('Reference evaluation item already has a terminal failure.',409)
        if item['result_id']:
            if item['result_id']!=result_id:
                raise DomainError('Reference evaluation item already has a saved result.',409)
        evaluation=context['evaluation']
        self.authenticate_item_result(evaluation,item,result_id)
        if item['result_id']:
            return self.get(evaluation_id)
        result=self.results.get(result_id)
        if (result['project_id']!=evaluation['project_id']
                or result['run_id']!=evaluation['run_id']
                or result['snapshot_id']!=evaluation['snapshot_id']
                or _question_key(result['question'])!=item['question_key']):
            raise DomainError('Reference result does not match the frozen evaluation item.',409)
        self._require_route_receipts(context['profile'],result['result'].get('execution_receipts'))
        timestamp=now()
        with self.db.connect(True) as connection:
            changed=connection.execute('''UPDATE reference_evaluation_items
                SET result_id=?,updated_at=? WHERE id=? AND evaluation_id=? AND result_id IS NULL''',
                (result_id,timestamp,item_id,evaluation_id)).rowcount
            if changed!=1:
                current=connection.execute('''SELECT result_id FROM reference_evaluation_items
                                              WHERE id=? AND evaluation_id=?''',
                                           (item_id,evaluation_id)).fetchone()
                if current is None or current['result_id']!=result_id:
                    raise DomainError('Reference evaluation item changed; refresh before executing.',409)
            connection.execute('UPDATE reference_evaluations SET updated_at=? WHERE id=?',
                               (timestamp,evaluation_id))
        return self.get(evaluation_id)
