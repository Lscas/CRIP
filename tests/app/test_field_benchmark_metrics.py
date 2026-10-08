import json
import itertools
import math
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.field_benchmark_metrics import summarize

def test_answerable_accuracy_excludes_refusals_and_unreviewed_records():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'CORRECT_COMPLETE','elapsed_seconds':4,'cached':False},
        {'answerability':'ANSWERABLE','judgment':'PARTIAL','elapsed_seconds':9,'cached':False},
        {'answerability':'ANSWERABLE','judgment':'INAPPROPRIATE_REFUSAL','elapsed_seconds':6,'cached':True},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':8,'cached':False},
        {'answerability':'UNANSWERABLE','judgment':'APPROPRIATE_REFUSAL','elapsed_seconds':3,'cached':False},
    ])
    assert result['answerable']['reviewed_count']==3
    assert result['answerable']['correct_complete_rate']==pytest.approx(1/3)
    assert result['unanswerable']['appropriate_refusal_rate']==1
    assert result['judgment_counts']['UNREVIEWED']==1

def test_total_and_stage_latency_are_separate_and_cache_is_reported():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'CORRECT_COMPLETE','elapsed_seconds':2,'cached':False,
         'stages':{'retrieval':1,'answer':1},'supplement_requests':2,'human_minutes':1.5},
        {'answerability':'UNANSWERABLE','judgment':'APPROPRIATE_REFUSAL','elapsed_seconds':10,'cached':True,
         'stages':{'retrieval':4,'answer':6},'human_minutes':.5},
    ])
    assert result['latency_seconds']['p50']==2 and result['latency_seconds']['p95']==10
    stage=result['latency_seconds']['by_stage']['retrieval']
    assert stage['p50']==1.0 and stage['p95']==4.0 and stage['sample_count']==2
    assert result['latency_seconds']['mean']==6
    assert result['latency_seconds']['fresh']['mean']==2
    assert result['latency_seconds']['cached']['mean']==10
    assert result['report_version']=='field-benchmark-metrics-2'
    assert result['execution_cache']=={
        'fresh_count':1,'cached_count':1,'fresh_elapsed_seconds':2.0,'cached_elapsed_seconds':10.0,
        'observed_question_count':2,'unknown_question_count':0,'unknown_count':0,'unknown_elapsed_seconds':0}
    assert result['supplement_requests_total'] is None
    assert result['evidence_loop']['supplement_requests']['observed_total']==2
    assert result['human_minutes_total']==2

def test_unknown_is_not_a_pass_and_refusal_scope_is_checked():
    result=summarize([{'answerability':'UNANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,'cached':False}])
    assert result['unanswerable']['reviewed_count']==0
    assert result['unanswerable']['appropriate_refusal_rate'] is None
    with pytest.raises(ValueError,match='only valid'):
        summarize([{'answerability':'ANSWERABLE','judgment':'APPROPRIATE_REFUSAL','elapsed_seconds':1,'cached':False}])


@pytest.mark.parametrize('field',['elapsed_seconds','human_minutes','stages'])
@pytest.mark.parametrize('invalid',[float('nan'),float('inf'),float('-inf'),True])
def test_nonfinite_metrics_never_produce_misleading_json_or_statistics(field,invalid):
    row={'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1}
    row[field]={'answer':invalid} if field=='stages' else invalid
    with pytest.raises(ValueError):summarize([row])


def test_evidence_requests_rounds_and_unknown_measurements_have_separate_denominators():
    rows=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':3,
           'supplement_requests':2,'supplement_rounds':1,'model_decisions':2},
          {'answerability':'ANSWERABLE','judgment':'CORRECT_COMPLETE','elapsed_seconds':5}]
    result=summarize(rows)
    assert result['evidence_loop']['supplement_requests']=={
        'observed_question_count':1,'unknown_question_count':1,
        'known_zero_question_count':0,'known_nonzero_question_count':1,
        'observed_total':2,'total':None,'mean_per_observed_question':2}
    assert result['evidence_loop']['supplement_rounds']['mean_per_observed_question']==1
    assert result['review_coverage']['reviewed_rate']==.5
    assert summarize([])['latency_seconds']['fresh']['mean'] is None
    with pytest.raises(ValueError,match='only valid'):
        summarize([{'answerability':'UNANSWERABLE','judgment':'CORRECT_COMPLETE','elapsed_seconds':1}])


def test_missing_null_and_explicit_zero_stay_distinct_in_cache_human_and_counters():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2,
         'cached':None,'human_minutes':None,'supplement_requests':None},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':3,
         'cached':False,'human_minutes':0,'supplement_requests':0},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':4,
         'cached':True,'human_minutes':2,'supplement_requests':3},
    ])
    assert result['execution_cache']['fresh_count']==result['execution_cache']['cached_count']==1
    assert result['execution_cache']['unknown_count']==2
    assert result['latency_seconds']['unknown']['sample_count']==2
    assert result['human_minutes']=={
        'observed_question_count':2,'unknown_question_count':2,
        'known_zero_question_count':1,'known_nonzero_question_count':1,
        'observed_total':2,'total':None,'mean_per_observed_question':1}
    assert result['human_minutes_total'] is None
    assert result['evidence_loop']['supplement_requests']['known_zero_question_count']==1
    assert result['evidence_loop']['supplement_requests']['total'] is None
    assert result['supplement_requests_total'] is None


def test_empty_partial_and_all_unknown_numeric_summaries_keep_exact_totals():
    empty=summarize([])
    assert empty['human_minutes']['observed_total']==empty['human_minutes']['total']==0
    assert empty['human_minutes']['mean_per_observed_question'] is None
    assert empty['evidence_loop']['supplement_requests']['total']==0
    partial=summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                       'supplement_requests':2},
                       {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2}])
    assert partial['evidence_loop']['supplement_requests']['observed_total']==2
    assert partial['evidence_loop']['supplement_requests']['total'] is None
    unknown=summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1}])
    assert unknown['human_minutes']['observed_total']==0 and unknown['human_minutes']['total'] is None


@pytest.mark.parametrize('field',[
    'supplement_requests','supplement_rounds','model_decisions',
    'requested_supplement_rounds','requested_supplement_requests',
    'accepted_supplement_rounds','accepted_supplement_requests',
])
def test_every_optional_counter_keeps_missing_null_and_zero_distinct(field):
    rows=[
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2,field:None},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':3,field:0},
    ]
    summary=summarize(rows)['evidence_loop'][field]
    assert summary=={
        'observed_question_count':1,'unknown_question_count':2,
        'known_zero_question_count':1,'known_nonzero_question_count':0,
        'observed_total':0,'total':None,'mean_per_observed_question':0.0}
    if field=='supplement_requests':
        assert summarize(rows)['supplement_requests_total'] is None


def test_legacy_top_totals_are_exact_only_when_every_value_is_observed():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
         'supplement_requests':0,'human_minutes':0},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2,
         'supplement_requests':3,'human_minutes':2},
    ])
    assert result['supplement_requests_total']==3 and result['human_minutes_total']==2


@pytest.mark.parametrize('field,value',[
    ('cached',1),('human_minutes',True),('human_minutes',float('nan')),
    ('supplement_requests',True),('requested_supplement_rounds',-1),
    ('accepted_supplement_requests',1.5),('model_decisions',True),
])
def test_optional_metric_values_are_strict_when_observed(field,value):
    row={'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:value}
    with pytest.raises(ValueError):summarize([row])


@pytest.mark.parametrize('field',[
    'requested_supplement_rounds','requested_supplement_requests',
])
def test_accepted_counters_cannot_exceed_requested_when_both_known(field):
    accepted=field.replace('requested','accepted')
    row={'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:1,accepted:2}
    with pytest.raises(ValueError,match='cannot exceed'):summarize([row])


def test_requested_and_accepted_counters_remain_independent_and_do_not_infer_new_evidence():
    result=summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                       'requested_supplement_rounds':2,'requested_supplement_requests':3,
                       'accepted_supplement_rounds':1,'accepted_supplement_requests':1}])
    assert result['evidence_loop']['requested_supplement_requests']['total']==3
    assert result['evidence_loop']['accepted_supplement_requests']['total']==1
    assert result['counter_semantics']['actual_new_evidence']=='NOT_OBSERVED_NOT_INFERRED'
    assert 'actual_new_evidence_total' not in result


@pytest.mark.parametrize('field',[
    'requested_supplement_rounds','requested_supplement_requests',
    'accepted_supplement_rounds','accepted_supplement_requests',
])
def test_new_counters_keep_missing_null_zero_and_known_nonzero_distinct(field):
    rows=[
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:None},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:0},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:2},
    ]
    assert summarize(rows)['evidence_loop'][field]=={
        'observed_question_count':2,'unknown_question_count':2,
        'known_zero_question_count':1,'known_nonzero_question_count':1,
        'observed_total':2,'total':None,'mean_per_observed_question':1}


@pytest.mark.parametrize('field',[
    'requested_supplement_rounds','requested_supplement_requests',
    'accepted_supplement_rounds','accepted_supplement_requests',
])
def test_new_counters_allow_one_side_observed_without_inference(field):
    result=summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,field:1}])
    counterpart=field.replace('requested','accepted') if field.startswith('requested') else field.replace('accepted','requested')
    assert result['evidence_loop'][field]['total']==1
    assert result['evidence_loop'][counterpart]['unknown_question_count']==1


@pytest.mark.parametrize('mode,cached',[
    ('FRESH',True),('CACHED',False),('REPLAY',True),('REPLAY',False),('MIXED',True),('MIXED',False),
])
def test_execution_mode_rejects_contradictory_cache_observations(mode,cached):
    with pytest.raises(ValueError,match='contradicts'):
        summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                   'execution_mode':mode,'cached':cached}])


def test_execution_mode_and_cache_observations_do_not_infer_each_other():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,'cached':False},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2,'execution_mode':'FRESH'},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':3,'execution_mode':'REPLAY'},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':4,'execution_mode':None},
    ])
    assert result['execution_cache']['fresh_count']==1 and result['execution_cache']['unknown_count']==3
    assert result['execution_mode_counts']=={'FRESH':1,'CACHED':0,'REPLAY':1,'MIXED':0,'UNKNOWN':2}
    assert result['latency_seconds']['by_execution_mode']['FRESH']['sample_count']==1
    assert result['latency_seconds']['by_execution_mode']['UNKNOWN']['sample_count']==2


@pytest.mark.parametrize('invalid',[True,0,'NOT_A_MODE'])
def test_execution_mode_rejects_invalid_observations(invalid):
    with pytest.raises(ValueError,match='execution_mode'):
        summarize([{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                   'execution_mode':invalid}])


def test_all_execution_modes_have_independent_latency_buckets():
    rows=[
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
         'execution_mode':'FRESH','cached':False},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':2,
         'execution_mode':'CACHED','cached':True},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':3,
         'execution_mode':'REPLAY','cached':None},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':4,
         'execution_mode':'MIXED'},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':5,
         'execution_mode':'UNKNOWN'},
    ]
    result=summarize(rows)
    assert result['execution_mode_counts']=={'FRESH':1,'CACHED':1,'REPLAY':1,'MIXED':1,'UNKNOWN':1}
    assert {mode:value['mean'] for mode,value in result['latency_seconds']['by_execution_mode'].items()}=={
        'FRESH':1,'CACHED':2,'REPLAY':3,'MIXED':4,'UNKNOWN':5}


@pytest.mark.parametrize('field,rows',[
    ('elapsed_seconds',[{'elapsed_seconds':1e308},{'elapsed_seconds':1e308}]),
    ('human_minutes',[{'elapsed_seconds':1,'human_minutes':1e308},{'elapsed_seconds':1,'human_minutes':1e308}]),
    ('stages',[{'elapsed_seconds':1,'stages':{'answer':1e308}},{'elapsed_seconds':1,'stages':{'answer':1e308}}]),
])
def test_finite_inputs_that_overflow_aggregation_fail_closed(field,rows):
    records=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED',**row} for row in rows]
    with pytest.raises(ValueError,match='aggregation must remain finite'):
        summarize(records)


def test_incorrect_records_still_contribute_to_elapsed_time_and_cli_writes_strict_json(tmp_path):
    records={'records':[{'answerability':'ANSWERABLE','judgment':'INCORRECT','elapsed_seconds':7,
                         'cached':False,'execution_mode':'FRESH'}]}
    result=summarize(records['records'])
    assert result['latency_seconds']['mean']==7 and result['execution_mode_counts']['FRESH']==1
    input_path=tmp_path/'records.json';output_path=tmp_path/'metrics.json'
    input_path.write_text(json.dumps(records),encoding='utf-8')
    completed=subprocess.run([sys.executable,'scripts/field_benchmark_metrics.py',str(input_path),
                              '--output',str(output_path)],capture_output=True,text=True,check=False)
    assert completed.returncode==0,completed.stderr
    rendered=json.loads(output_path.read_text(encoding='utf-8'))
    assert rendered['report_version']=='field-benchmark-metrics-2'
    assert 'NaN' not in output_path.read_text(encoding='utf-8')


def test_cli_rejects_aggregate_overflow_without_creating_output(tmp_path):
    records={'records':[
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,'human_minutes':1e308},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,'human_minutes':1e308},
    ]}
    input_path=tmp_path/'overflow.json';output_path=tmp_path/'must-not-exist.json'
    input_path.write_text(json.dumps(records),encoding='utf-8')
    completed=subprocess.run([sys.executable,'scripts/field_benchmark_metrics.py',str(input_path),
                              '--output',str(output_path)],capture_output=True,text=True,check=False)
    assert completed.returncode!=0 and not output_path.exists()


@pytest.mark.parametrize('field',["elapsed_seconds","human_minutes","stages"])
def test_float_aggregation_is_order_independent(field):
    values=(1e16,1.0,1.0)
    expected=math.fsum(values)
    for ordered in set(itertools.permutations(values)):
        if field=='elapsed_seconds':
            records=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':value}
                     for value in ordered]
        elif field=='human_minutes':
            records=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                      'human_minutes':value} for value in ordered]
        else:
            records=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                      'stages':{'answer':value}} for value in ordered]
        result=summarize(records)
        if field=='human_minutes':
            observed=result['human_minutes']['observed_total']
        elif field=='elapsed_seconds':
            observed=result['execution_cache']['unknown_elapsed_seconds']
        else:
            observed=result['latency_seconds']['by_stage']['answer']['mean']*len(records)
        assert observed==expected


def test_mixed_human_minute_aggregation_is_order_independent():
    values=(1e16,1,1)
    expected=math.fsum(values)
    for ordered in set(itertools.permutations(values)):
        records=[{'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
                  'human_minutes':value} for value in ordered]
        assert summarize(records)['human_minutes']['observed_total']==expected


def test_strict_integer_counter_totals_do_not_round_through_float():
    result=summarize([
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
         'supplement_requests':10**16},
        {'answerability':'ANSWERABLE','judgment':'UNREVIEWED','elapsed_seconds':1,
         'supplement_requests':2},
    ])
    assert result['evidence_loop']['supplement_requests']['observed_total']==10**16+2
