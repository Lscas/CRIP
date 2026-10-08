"""Offline metrics for independently reviewed field-worker benchmark records."""
from __future__ import annotations
import argparse
import json
import math
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT=Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:sys.path.insert(0,str(_ROOT))

JUDGMENTS={'CORRECT_COMPLETE','PARTIAL','INCORRECT','APPROPRIATE_REFUSAL','INAPPROPRIATE_REFUSAL','UNREVIEWED'}
ANSWERABILITY={'ANSWERABLE','UNANSWERABLE'}
EXECUTION_MODES=('FRESH','CACHED','REPLAY','MIXED','UNKNOWN')
COUNTERS=('supplement_requests','supplement_rounds','model_decisions',
          'requested_supplement_rounds','requested_supplement_requests',
          'accepted_supplement_rounds','accepted_supplement_requests')
SMOKE3='reference-live-smoke-3'
ADAPTER_VERSION='reference-live-smoke-metrics-adapter-1'
REVIEW_VERSION='reference-live-smoke-review-1'

def _percentile(values:list[float], p:float):
    if not values:return None
    return sorted(values)[max(0,math.ceil(len(values)*p)-1)]

def _finite_sum(values):
    values=list(values)
    if all(type(value) is int for value in values):return sum(values)
    try:total=math.fsum(values)
    except OverflowError as exc:raise ValueError('Metric aggregation must remain finite.') from exc
    if not math.isfinite(total):
        raise ValueError('Metric aggregation must remain finite.')
    return total

def _rate(n:int, d:int):
    if not d:return None
    try:value=n/d
    except OverflowError as exc:raise ValueError('Metric aggregation must remain finite.') from exc
    if not math.isfinite(value):raise ValueError('Metric aggregation must remain finite.')
    return value

def _distribution(values:list[float])->dict:
    total=_finite_sum(values)
    return {'sample_count':len(values),'mean':_rate(total,len(values)),
            'p50':_percentile(values,.50),'p95':_percentile(values,.95),
            'minimum':min(values) if values else None,'maximum':max(values) if values else None}

def _duration(value)->bool:
    return type(value) in (int,float) and math.isfinite(value) and value>=0

def _optional_count(record:dict,field:str):
    value=record.get(field)
    if value is None:return None
    if type(value) is not int or value<0:
        raise ValueError(f'{field} must be a non-negative integer.')
    return value

def _observed_summary(records:list[dict],field:str)->dict:
    values=[record[field] for record in records if record.get(field) is not None]
    observed=len(values);unknown=len(records)-observed;total=_finite_sum(values)
    return {'observed_question_count':observed,'unknown_question_count':unknown,
            'known_zero_question_count':sum(value==0 for value in values),
            'known_nonzero_question_count':sum(value>0 for value in values),
            'observed_total':total,'total':total if unknown==0 else None,
            'mean_per_observed_question':_rate(total,observed)}

def _record(record:dict):
    if not isinstance(record,dict):raise ValueError('Each benchmark record must be an object.')
    if record.get('answerability') not in ANSWERABILITY:raise ValueError('answerability must be ANSWERABLE or UNANSWERABLE.')
    if record.get('judgment') not in JUDGMENTS:raise ValueError('judgment is invalid.')
    elapsed=record.get('elapsed_seconds')
    if not _duration(elapsed):raise ValueError('elapsed_seconds must be finite and non-negative.')
    cached=record.get('cached')
    if cached is not None and type(cached) is not bool:raise ValueError('cached must be boolean or null.')
    for field in (*COUNTERS,'human_minutes'):
        value=record.get(field)
        if value is not None and (not _duration(value) if field=='human_minutes'
                                  else type(value) is not int or value<0):
            raise ValueError(f'{field} must be a finite non-negative number.' if field=='human_minutes'
                             else f'{field} must be a non-negative integer.')
    requested_rounds=_optional_count(record,'requested_supplement_rounds')
    accepted_rounds=_optional_count(record,'accepted_supplement_rounds')
    requested_requests=_optional_count(record,'requested_supplement_requests')
    accepted_requests=_optional_count(record,'accepted_supplement_requests')
    if ((requested_rounds is not None and accepted_rounds is not None and accepted_rounds>requested_rounds)
            or (requested_requests is not None and accepted_requests is not None
                and accepted_requests>requested_requests)):
        raise ValueError('accepted supplement counters cannot exceed requested counters.')
    mode=record.get('execution_mode')
    if mode is None:mode='UNKNOWN'
    if mode not in EXECUTION_MODES:raise ValueError('execution_mode is invalid.')
    if ((mode=='FRESH' and cached is True) or (mode=='CACHED' and cached is False)
            or (mode in ('REPLAY','MIXED') and cached is not None)):
        raise ValueError('execution_mode contradicts cached observation.')
    stages=record.get('stages',{})
    if not isinstance(stages,dict):raise ValueError('stages must be an object.')
    for name,value in stages.items():
        if not isinstance(name,str) or not name or not _duration(value):
            raise ValueError('stage durations must be finite non-negative numbers with a name.')

def summarize(records:list[dict])->dict:
    for record in records:_record(record)
    counts=Counter(record['judgment'] for record in records)
    answerable=[r for r in records if r['answerability']=='ANSWERABLE' and r['judgment']!='UNREVIEWED']
    unanswerable=[r for r in records if r['answerability']=='UNANSWERABLE' and r['judgment']!='UNREVIEWED']
    if any(r['judgment']=='APPROPRIATE_REFUSAL' for r in answerable):
        raise ValueError('APPROPRIATE_REFUSAL is only valid for UNANSWERABLE records.')
    if any(r['judgment']=='INAPPROPRIATE_REFUSAL' for r in unanswerable):
        raise ValueError('INAPPROPRIATE_REFUSAL is only valid for ANSWERABLE records.')
    if any(r['judgment'] in ('CORRECT_COMPLETE','PARTIAL') for r in unanswerable):
        raise ValueError('Answer-quality judgments are only valid for ANSWERABLE records.')
    stages=defaultdict(list)
    for record in records:
        for name,value in record.get('stages',{}).items():stages[name].append(float(value))
    elapsed=[float(r['elapsed_seconds']) for r in records]
    fresh=[r for r in records if r.get('cached') is False]
    cached=[r for r in records if r.get('cached') is True]
    cache_unknown=[r for r in records if r.get('cached') is None]
    by_mode={mode:[r for r in records if (r.get('execution_mode') or 'UNKNOWN')==mode]
             for mode in EXECUTION_MODES}
    counters={field:_observed_summary(records,field) for field in COUNTERS}
    human_minutes=_observed_summary(records,'human_minutes')
    return {
        'report_version':'field-benchmark-metrics-2',
        'record_count':len(records),
        'review_coverage':{'reviewed_count':len(answerable)+len(unanswerable),
                           'unreviewed_count':counts['UNREVIEWED'],
                           'reviewed_rate':_rate(len(answerable)+len(unanswerable),len(records))},
        'judgment_counts':{name:counts[name] for name in sorted(JUDGMENTS)},
        'answerable':{
            'reviewed_count':len(answerable),
            'correct_complete_count':sum(r['judgment']=='CORRECT_COMPLETE' for r in answerable),
            'partial_count':sum(r['judgment']=='PARTIAL' for r in answerable),
            'incorrect_count':sum(r['judgment']=='INCORRECT' for r in answerable),
            'inappropriate_refusal_count':sum(r['judgment']=='INAPPROPRIATE_REFUSAL' for r in answerable),
            'correct_complete_rate':_rate(sum(r['judgment']=='CORRECT_COMPLETE' for r in answerable),len(answerable)),
        },
        'unanswerable':{
            'reviewed_count':len(unanswerable),
            'appropriate_refusal_count':sum(r['judgment']=='APPROPRIATE_REFUSAL' for r in unanswerable),
            'incorrect_count':sum(r['judgment']=='INCORRECT' for r in unanswerable),
            'appropriate_refusal_rate':_rate(sum(r['judgment']=='APPROPRIATE_REFUSAL' for r in unanswerable),len(unanswerable)),
        },
        'latency_seconds':{**_distribution(elapsed),
                            'fresh':_distribution([float(r['elapsed_seconds']) for r in fresh]),
                            'cached':_distribution([float(r['elapsed_seconds']) for r in cached]),
                            'unknown':_distribution([float(r['elapsed_seconds']) for r in cache_unknown]),
                            'by_execution_mode':{mode:_distribution([float(r['elapsed_seconds']) for r in rows])
                                                 for mode,rows in by_mode.items()},
                            'by_stage':{name:_distribution(values) for name,values in sorted(stages.items())}},
        'evidence_loop':counters,
        'counter_semantics':{
            'supplement_requests':'CALLER_DEFINED_LEGACY_NOT_CROSS_VERSION_COMPARABLE',
            'supplement_rounds':'CALLER_DEFINED_LEGACY_NOT_CROSS_VERSION_COMPARABLE',
            'model_decisions':'CALLER_DEFINED_LEGACY_NOT_CROSS_VERSION_COMPARABLE',
            'requested_supplement_rounds':'EXPLICIT_REQUESTED_NOT_INFERRED',
            'requested_supplement_requests':'EXPLICIT_REQUESTED_NOT_INFERRED',
            'accepted_supplement_rounds':'EXPLICIT_ACCEPTED_NOT_INFERRED',
            'accepted_supplement_requests':'EXPLICIT_ACCEPTED_NOT_INFERRED',
            'actual_new_evidence':'NOT_OBSERVED_NOT_INFERRED',
        },
        'supplement_requests_total':counters['supplement_requests']['total'],
        'human_minutes':human_minutes,
        'human_minutes_total':human_minutes['total'],
        'execution_cache':{'fresh_count':len(fresh),'cached_count':len(cached),
                            'fresh_elapsed_seconds':_finite_sum(float(r['elapsed_seconds']) for r in fresh),
                            'cached_elapsed_seconds':_finite_sum(float(r['elapsed_seconds']) for r in cached),
                            'observed_question_count':len(fresh)+len(cached),
                            'unknown_question_count':len(cache_unknown),
                            'unknown_count':len(cache_unknown),
                            'unknown_elapsed_seconds':_finite_sum(float(r['elapsed_seconds']) for r in cache_unknown)},
        'execution_mode_counts':{mode:len(rows) for mode,rows in by_mode.items()},
    }

def _strict_json(raw:bytes)->dict:
    def duplicate_free(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('JSON object keys must be unique.')
            value[key]=item
        return value
    def nonfinite(value):raise ValueError('JSON must not contain non-finite values.')
    def finite(value):
        if type(value) is float and not math.isfinite(value):raise ValueError('JSON must not contain non-finite values.')
        if isinstance(value,dict):
            for item in value.values():finite(item)
        elif isinstance(value,list):
            for item in value:finite(item)
    if not isinstance(raw,bytes):raise ValueError('Input must be raw bytes.')
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=duplicate_free,parse_constant=nonfinite)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError('Input must be UTF-8 JSON.') from exc
    if not isinstance(value,dict):raise ValueError('Input must be a JSON object.')
    finite(value)
    return value

def _raw_json(path:Path)->tuple[bytes,dict]:
    raw=path.read_bytes();value=_strict_json(raw)
    return raw,value

def _bound_report(raw:bytes,report:dict)->dict:
    parsed=_strict_json(raw)
    if parsed!=report:raise ValueError('The report object must exactly match the supplied raw bytes.')
    return parsed

def _is_int(value):return type(value) is int

def _count(value,name):
    if not _is_int(value) or value<0:raise ValueError(f'{name} must be a non-negative integer.')
    return value

def _smoke_identity(report:dict)->dict:
    if report.get('report_version')!=SMOKE3:raise ValueError('Only complete reference-live-smoke-3 reports are supported.')
    policy=report.get('policy')
    if (not isinstance(policy,dict) or any(policy.get(key) is not False for key in (
            'source_text_in_report','private_reasoning_saved','human_adjudications_written'))):
        raise ValueError('The smoke report policy is not safe for offline adaptation.')
    if (policy.get('supplement_count_semantics')!='REQUESTED_AND_ACCEPTED_SEPARATE_LEGACY_ALIASES_ACCEPTED'
            or policy.get('automatic_retries')!=0 or not _is_int(policy.get('automatic_retries'))):
        raise ValueError('The smoke report policy does not define compatible supplement semantics.')
    if report.get('unresolved_calls_at_completion')!=0 or not _is_int(report.get('unresolved_calls_at_completion')):
        raise ValueError('The smoke report must have exactly zero unresolved calls.')
    required=('evaluation_id','project_id','run_id','snapshot_id','selector_version','profile')
    if any(not isinstance(report.get(key),str) or not report[key] for key in required[:-1]):
        raise ValueError('The smoke report identity is malformed.')
    if report['selector_version']!='literal-page-selector-9':
        raise ValueError('Only named selector-v9 smoke reports are supported.')
    profile_value=report['profile']
    if (not isinstance(profile_value,dict) or not isinstance(profile_value.get('profile_id'),str)
            or not profile_value['profile_id']):
        raise ValueError('The smoke report profile is malformed.')
    from app.reference_text_profiles import profile
    canonical=profile(profile_value['profile_id'])
    if json.dumps(profile_value,sort_keys=True,separators=(',',':'))!=json.dumps(canonical,sort_keys=True,separators=(',',':')):
        raise ValueError('The smoke report profile is not canonical.')
    items=report.get('items')
    if not isinstance(items,list) or not items:raise ValueError('The smoke report must contain items.')
    pairs=[]
    for item in items:
        if (not isinstance(item,dict) or item.get('state') not in ('COMPLETE','FAILED')
                or not isinstance(item.get('question_id'),str) or not item['question_id']
                or not isinstance(item.get('item_id'),str) or not item['item_id']):
            raise ValueError('The smoke report item is not terminal and identified.')
        pairs.append({'question_id':item['question_id'],'item_id':item['item_id']})
    if (len({item['question_id'] for item in pairs})!=len(pairs)
            or len({item['item_id'] for item in pairs})!=len(pairs)):
        raise ValueError('The smoke report item identity is not unique.')
    return {'report_version':SMOKE3,'evaluation_id':report['evaluation_id'],'project_id':report['project_id'],
            'run_id':report['run_id'],'snapshot_id':report['snapshot_id'],
            'selector_version':report['selector_version'],'profile_id':canonical['profile_id'],'items':pairs}

def review_template_from_smoke_bytes(raw:bytes, report:dict)->dict:
    identity=_smoke_identity(_bound_report(raw,report))
    return {'review_version':REVIEW_VERSION,'source_binding':{
        'report_sha256':hashlib.sha256(raw).hexdigest(),'identity':identity},
        'reviews':[{'question_id':item['question_id'],'item_id':item['item_id'],
                    'answerability':None,'judgment':'UNREVIEWED','human_minutes':None}
                   for item in identity['items']]}

def review_template_from_smoke_file(path:Path)->dict:
    raw,report=_raw_json(path);return review_template_from_smoke_bytes(raw,report)

def _review_records(sidecar:dict, raw:bytes, identity:dict)->list[dict]:
    if set(sidecar)!={'review_version','source_binding','reviews'} or sidecar.get('review_version')!=REVIEW_VERSION:
        raise ValueError('The review sidecar has an unsupported shape or version.')
    binding=sidecar['source_binding']
    if (not isinstance(binding,dict) or set(binding)!={'report_sha256','identity'}
            or binding['report_sha256']!=hashlib.sha256(raw).hexdigest() or binding['identity']!=identity):
        raise ValueError('The review sidecar does not bind the exact smoke-report bytes and identity.')
    reviews=sidecar['reviews']
    if not isinstance(reviews,list) or len(reviews)!=len(identity['items']):
        raise ValueError('The review sidecar does not cover every smoke item exactly once.')
    keys={'question_id','item_id','answerability','judgment','human_minutes'}
    pairs=[{'question_id':entry['question_id'],'item_id':entry['item_id']} for entry in reviews
           if isinstance(entry,dict) and set(entry)==keys]
    if pairs!=identity['items']:
        raise ValueError('The review sidecar item order or identity differs from the smoke report.')
    for entry in reviews:
        if entry['answerability'] not in ANSWERABILITY or entry['judgment'] not in JUDGMENTS:
            raise ValueError('The review sidecar requires answerability and a valid judgment.')
        if entry['human_minutes'] is not None and not _duration(entry['human_minutes']):
            raise ValueError('human_minutes must be a finite non-negative number.')
    return reviews

def _complete_counts(item:dict)->dict:
    decisions=_count(item.get('model_decisions'),'model_decisions')
    if decisions not in (1,2,3) or item.get('receipt_scope')!='COMPLETE_CHAIN' or _count(item.get('receipt_count'),'receipt_count')!=decisions:
        raise ValueError('Complete-chain item counters are inconsistent.')
    saved=_count(item.get('model_call_count'),'model_call_count')
    if (item.get('model_call_count_exact') is not True or item.get('model_call_count_scope')!='SAVED_EXECUTION'
            or saved>decisions or type(item.get('all_receipts_cached')) is not bool
            or item['all_receipts_cached'] != (saved==0)):
        raise ValueError('Complete-chain saved execution counters are inconsistent.')
    counts={}
    for name in ('requested_supplement_rounds','requested_supplement_requests',
                 'accepted_supplement_rounds','accepted_supplement_requests'):
        counts[name]=_count(item.get(name),name)
    requested_rounds=counts['requested_supplement_rounds'];accepted_rounds=counts['accepted_supplement_rounds']
    requested_requests=counts['requested_supplement_requests'];accepted_requests=counts['accepted_supplement_requests']
    if (requested_rounds>3 or accepted_rounds>2 or requested_rounds>decisions or accepted_rounds>decisions
            or not requested_rounds<=requested_requests<=2*requested_rounds
            or not accepted_rounds<=accepted_requests<=2*accepted_rounds):
        raise ValueError('Complete-chain supplement counters are outside producer bounds.')
    if item['state']=='FAILED':
        valid=(requested_rounds==accepted_rounds==decisions-1 and requested_requests==accepted_requests)
    else:
        valid=((requested_rounds,accepted_rounds)==(decisions-1,decisions-1)
               or (decisions<=2 and (requested_rounds,accepted_rounds)==(decisions,decisions))
               or (decisions==3 and (requested_rounds,accepted_rounds)==(3,2)))
        if valid and requested_rounds==accepted_rounds:valid=requested_requests==accepted_requests
        elif valid:valid=1<=requested_requests-accepted_requests<=2
    if not valid:raise ValueError('Complete-chain supplement topology is inconsistent.')
    return {'model_decisions':decisions,**counts}

def _terminal_metadata(item:dict,*,decisions:int|None=None)->int:
    cached=item.get('terminal_receipt_cached');round_number=item.get('terminal_round')
    lower=item.get('terminal_new_call_lower_bound')
    if (type(cached) is not bool or type(round_number) is not int or round_number not in (1,2,3)
            or (decisions is not None and round_number!=decisions)
            or type(lower) is not int or lower!=int(cached is False)):
        raise ValueError('Terminal receipt metadata is inconsistent.')
    return lower

def _current_execution(item:dict, decisions:int|None)->tuple[str,bool|None,int|None,int|None]:
    replayed=item.get('replayed');terminal=item.get('already_terminal');exact=item.get('new_calls_this_run_exact')
    if type(replayed) is not bool or type(terminal) is not bool or type(exact) is not bool:
        raise ValueError('Current execution flags are malformed.')
    value=item.get('new_calls_this_run')
    if terminal and not replayed:raise ValueError('An already-terminal item must be authenticated as replayed.')
    if replayed:
        if exact is not True or value!=0 or not _is_int(value):
            raise ValueError('Replay current execution must be exactly zero calls.')
        return 'REPLAY',None,0,None
    if exact is False:
        if value is not None:raise ValueError('Unknown current execution cannot carry a call count.')
        lower=item.get('new_calls_this_run_lower_bound')
        if lower is not None:_count(lower,'terminal_new_call_lower_bound')
        return 'UNKNOWN',None,None,lower
    if not _is_int(value) or value<0 or decisions is None or value>decisions:
        raise ValueError('Exact current execution calls contradict the complete chain.')
    if value==0:return 'CACHED',True,value,None
    if value==decisions:return 'FRESH',False,value,None
    return 'MIXED',None,value,None

def adapt_smoke3_bytes(raw:bytes, report:dict, sidecar:dict)->dict:
    report=_bound_report(raw,report);identity=_smoke_identity(report);reviews=_review_records(sidecar,raw,identity)
    records=[];current=[];current_items=[];lower_bounds=[]
    for item,review in zip(report['items'],reviews):
        if not _duration(item.get('elapsed_seconds')):raise ValueError('Smoke item elapsed_seconds must be finite and non-negative.')
        complete=item.get('receipt_scope')=='COMPLETE_CHAIN'
        counts=_complete_counts(item) if complete else {}
        if complete:
            if 'new_calls_this_run_lower_bound' in item:
                raise ValueError('Complete-chain item cannot carry a current-call lower bound.')
            if item['state']=='COMPLETE' and any(name in item for name in (
                    'terminal_round','terminal_receipt_cached','terminal_new_call_lower_bound')):
                raise ValueError('Successful complete-chain item cannot carry failure terminal metadata.')
            if item['state']=='FAILED':
                terminal_lower=_terminal_metadata(item,decisions=counts['model_decisions'])
                saved_calls=item['model_call_count']
                decisions=counts['model_decisions']
                if ((item['terminal_receipt_cached'] is False and saved_calls<1)
                        or (item['terminal_receipt_cached'] is True and saved_calls>decisions-1)):
                    raise ValueError('Complete failure saved terminal metadata is inconsistent.')
                lower_bounds.append({'question_id':item['question_id'],'item_id':item['item_id'],
                                     'terminal_new_call_lower_bound':terminal_lower})
        if not complete and item.get('receipt_scope')!='TERMINAL_ONLY':
            raise ValueError('Smoke item receipt scope is unsupported.')
        if not complete:
            forbidden={'model_decisions','requested_supplement_rounds','requested_supplement_requests',
                       'accepted_supplement_rounds','accepted_supplement_requests'}
            if (item['state']!='FAILED' or any(name in item for name in forbidden)
                    or _count(item.get('receipt_count'),'receipt_count')!=1
                    or item.get('model_call_count') is not None or item.get('model_call_count_exact') is not False
                    or item.get('model_call_count_scope')!='SAVED_EXECUTION'
                    ):
                raise ValueError('Terminal-only failure fields are inconsistent.')
            terminal_lower=_terminal_metadata(item)
            current_lower=item.get('new_calls_this_run_lower_bound')
            if (type(current_lower) is not int
                    or current_lower!=(0 if item.get('replayed') is True else terminal_lower)):
                raise ValueError('Terminal-only current-call lower bound is inconsistent.')
            lower_bounds.append({'question_id':item['question_id'],'item_id':item['item_id'],
                                 'terminal_new_call_lower_bound':terminal_lower})
        mode,cached,new_calls,lower=_current_execution(item,counts.get('model_decisions'))
        if (complete and item['state']=='FAILED' and item['replayed'] is False
                and item['new_calls_this_run_exact'] is True
                and new_calls!=item['model_call_count']):
            raise ValueError('Complete failure current execution differs from its saved chain.')
        record={'answerability':review['answerability'],'judgment':review['judgment'],
                'elapsed_seconds':item['elapsed_seconds'],'execution_mode':mode,'cached':cached,
                'human_minutes':review['human_minutes'],**counts}
        records.append(record);current.append({'current_model_calls':new_calls})
        current_items.append({'question_id':item['question_id'],'item_id':item['item_id'],
                              'current_model_calls':new_calls})
    return {'adapter_version':ADAPTER_VERSION,'source_binding':{
                'report_sha256':hashlib.sha256(raw).hexdigest(),'identity':identity},
            'review_authentication':'CALLER_SUPPLIED_NOT_AUTHENTICATED',
            'source_authentication':'CALLER_SUPPLIED_FILE_NOT_AUTHENTICATED',
            'observation_scopes':{
                'elapsed_seconds':'RUNNER_PREVIEW_EXECUTE_TO_TERMINAL_CHECK',
                'model_decisions':'SAVED_COMPLETE_CHAIN_ONLY','current_model_calls':'CURRENT_EXECUTE_EXACT_ONLY',
                'requested_supplement_rounds':'SAVED_COMPLETE_CHAIN_ONLY',
                'requested_supplement_requests':'SAVED_COMPLETE_CHAIN_ONLY',
                'accepted_supplement_rounds':'SAVED_COMPLETE_CHAIN_ONLY',
                'accepted_supplement_requests':'SAVED_COMPLETE_CHAIN_ONLY',
                'terminal_new_call_lower_bound':'SAVED_TERMINAL_RECEIPT_LOWER_BOUND_NOT_EXACT'},
            'records':records,'summary':summarize(records),
            'current_execution':{'items':current_items,
                                 'current_model_calls':_observed_summary(current,'current_model_calls')},
            'source_observations':{'terminal_new_call_lower_bounds':lower_bounds}}

def adapt_smoke3_files(smoke_path:Path, review_path:Path)->dict:
    raw,report=_raw_json(smoke_path);_,sidecar=_raw_json(review_path)
    return adapt_smoke3_bytes(raw,report,sidecar)

def _write_new_output(path:Path, encoded:str, inputs:tuple[Path,...]):
    if path.exists() or any(path.resolve()==source.resolve() for source in inputs):
        raise ValueError('Output must be a new path distinct from every input.')
    with path.open('x',encoding='utf-8') as output:output.write(encoded)

def main():
    parser=argparse.ArgumentParser(description='Summarize offline field benchmark records.')
    parser.add_argument('input',type=Path);parser.add_argument('--output',type=Path)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--smoke3-review-template',action='store_true')
    modes.add_argument('--smoke3-review',type=Path)
    args=parser.parse_args()
    if args.smoke3_review_template:
        if args.output is None:parser.error('--smoke3-review-template requires --output.')
        result=review_template_from_smoke_file(args.input);inputs=(args.input,)
    elif args.smoke3_review:
        if args.output is None:parser.error('--smoke3-review requires --output.')
        result=adapt_smoke3_files(args.input,args.smoke3_review);inputs=(args.input,args.smoke3_review)
    else:
        data=json.loads(args.input.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or not isinstance(data.get('records'),list):raise ValueError('Input must contain a records list.')
        result=summarize(data['records']);inputs=()
    encoded=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n"
    if args.output:
        if inputs:_write_new_output(args.output,encoded,inputs)
        else:args.output.write_text(encoded,encoding='utf-8')
    else:print(encoded,end='')
if __name__=='__main__':main()
