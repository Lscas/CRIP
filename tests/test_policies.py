"""路由、费用与版本的离线规则测试。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import pytest
from contracts.runtime_rules import (load_json,route_task,make_chat_payload,quote_tokens,BudgetLedger,BudgetBlocked,RevisionClaim,select_latest,cache_key,sum_design_instances)

@pytest.mark.parametrize('task',load_json('config/model_routing.json')['programmatic_tasks'])
def test_programmatic_zero_llm(task):
    r=route_task(task);assert r.role=='programmatic' and r.model is None

@pytest.mark.parametrize('task',load_json('config/model_routing.json')['cheap_tasks'])
def test_simple_tasks_nonthinking_flash(task):
    r=route_task(task);assert r.role=='cheap' and r.thinking=='disabled' and r.model=='deepseek-v4-flash'

def test_cheap_payload_explicitly_disables_thinking():
    p=make_chat_payload(route_task('classify'),'只输出json','合成证据')
    assert p['thinking']=={'type':'disabled'} and p['response_format']['type']=='json_object'
    assert 'tools' not in p and 'reasoning_effort' not in p

def test_missing_evidence_does_not_escalate():
    assert route_task('classify',evidence_complete=False,ambiguous=True).role=='NEEDS_REVIEW'

def test_vision_not_sent_to_text_model():
    assert route_task('classify',has_image=True,vision_available=False).role=='NEEDS_REVIEW'
    assert route_task('classify',has_image=True,vision_available=True).role=='vision'

def test_reasoning_low_not_automatic_high_model():
    r=route_task('cross_document_reconcile');p=make_chat_payload(r,'只输出json','合成关系',max_tokens=3000)
    assert r.model=='deepseek-v4-flash' and p['reasoning_effort']=='low'

def test_high_model_disabled_even_if_requested():
    assert route_task('classify',requested_high=True,high_approved=True).role=='NEEDS_REVIEW'

def test_call_limit_stops_loop():
    assert route_task('extract_joint',calls_used=3).role=='NEEDS_REVIEW'

def test_unknown_task_no_autonomous_call():
    assert route_task('send_purchase_order').role=='NEEDS_REVIEW'

def test_output_cap():
    assert make_chat_payload(route_task('extract_joint'),'json','x',max_tokens=8000)['max_tokens']==8000
    with pytest.raises(ValueError):make_chat_payload(route_task('classify'),'json','x',max_tokens=8001)

def test_budget_formula_total_output_not_double_count():
    assert quote_tokens(10_000_000,1_000_000,Decimal(3),Decimal(9),safety=Decimal(1))==Decimal(39)

def test_budget_300_hard_dispatch_cap():
    b=BudgetLedger();b.reserve('first',Decimal(290))
    with pytest.raises(BudgetBlocked):b.reserve('second',Decimal(11))
    assert b.outstanding==290

def test_settle_refunds_difference():
    b=BudgetLedger();b.reserve('a',Decimal(100));b.settle('a',Decimal(60));b.reserve('b',Decimal(240))
    assert b.spent+b.outstanding==300

def test_unknown_charge_stays_reserved():
    b=BudgetLedger();b.reserve('a',Decimal(300));b.mark_unknown('a')
    with pytest.raises(BudgetBlocked):b.reserve('retry',Decimal(1))
    with pytest.raises(ValueError):b.release_unbilled('a',confirmed_unbilled=False)

def test_confirmed_unbilled_can_release():
    b=BudgetLedger();b.reserve('a',Decimal(300));b.release_unbilled('a',confirmed_unbilled=True)
    assert b.spent==0 and b.outstanding==0

def test_idempotent_reserve_and_settle():
    b=BudgetLedger();b.reserve('a',Decimal(100));b.reserve('a',Decimal(100));assert b.outstanding==100
    b.settle('a',Decimal(60));b.settle('a',Decimal(60));assert b.spent==60

def test_actual_over_estimate_freezes_but_records():
    b=BudgetLedger();b.reserve('a',Decimal(10));b.settle('a',Decimal(11));assert b.spent==11 and b.frozen
    with pytest.raises(BudgetBlocked):b.reserve('b',Decimal(1))

def test_concurrent_reservations_do_not_oversubscribe():
    b=BudgetLedger()
    def attempt(i):
        try:b.reserve(str(i),Decimal(100));return True
        except BudgetBlocked:return False
    with ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(attempt,range(10)))
    assert sum(results)==3 and b.outstanding==300

def claims():
    return [RevisionClaim('EV-1','PAD-1|performance','X','2026-01-01',upload_time='2026-09-08'),RevisionClaim('EV-2','PAD-1|performance','Y','2026-02-01',upload_time='2026-03-01')]

def test_revision_uses_internal_date_not_upload():
    result=select_latest(claims());assert result.value=='Y' and result.selected_evidence_ids==('EV-2',)

@pytest.mark.parametrize('newdate',[None,'invalid','2026-01-01'])
def test_uncertain_revision_stays_unresolved(newdate):
    x=claims();x[1]=replace(x[1],internal_revision_date=newdate);assert select_latest(x).status=='UNRESOLVED'

def test_different_scope_not_superseded():
    x=claims();x[1]=replace(x[1],scope_key='OTHER');assert select_latest(x).status=='UNRESOLVED'

def test_rfi_question_not_new_requirement():
    x=claims();x[1]=replace(x[1],asserted=False);assert select_latest(x).value=='X'

def test_revision_sequence_reversal_flagged():
    x=claims();x[0]=replace(x[0],revision_sequence=3,document_family='S001');x[1]=replace(x[1],revision_sequence=2,document_family='S001');assert select_latest(x).status=='UNRESOLVED'

def test_cache_requires_scope():
    x=load_json('examples/cache-key-input.json');del x['project_id']
    with pytest.raises(ValueError):cache_key(x)

@pytest.mark.parametrize('key',['tenant_id','project_id','input_snapshot_id','prompt_version','model_snapshot','retrieval_version','assembly_rule_version','revision_policy_version'])
def test_cache_varies_with_scope_and_versions(key):
    x=load_json('examples/cache-key-input.json');first=cache_key(x);x[key]='CHANGED';assert first!=cache_key(x)

def test_multiple_views_same_instance_not_double_counted():
    rows=[{'instance_id':'DOOR-1','design_value':1,'unit':'EA'},{'instance_id':'DOOR-1','design_value':1,'unit':'EA'},{'instance_id':'DOOR-2','design_value':1,'unit':'EA'}]
    assert sum_design_instances(rows)==(Decimal(2),'EA')

def test_instance_quantity_conflict_does_not_auto_sum():
    rows=[{'instance_id':'A','design_value':1,'unit':'EA'},{'instance_id':'A','design_value':2,'unit':'EA'}]
    with pytest.raises(ValueError):sum_design_instances(rows)
