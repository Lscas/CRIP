"""原子要求到可审核记录的基线转换。复杂实体、选项与视觉关联仍待实现。"""
from __future__ import annotations
import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from app.db import dumps, now
from app.settings import VERSION, ROOT
from contracts.runtime_rules import (EvidenceScope,RevisionClaim,select_latest,wrap_candidate)

SCHEMA_VERSION='0.2.0'

def key(*values):return hashlib.sha256(dumps(values).encode()).hexdigest()[:24]

def missing(subject: str, reason: str, code: str, evidence_ids=None, target=None):
    return {'candidate_key':'MI-'+key(subject,reason),'subject':subject,
            'missing_field_or_document':reason,'blocking_reason':'当前内容审查无法完成，需要处理后重新分析。',
            'blocks_current_task':True,'reason_code':code,'related_evidence_ids':evidence_ids or [],
            'affected_output_keys':[target or subject],'search_state':'PARSE_UNAVAILABLE' if code=='PARSE_FAILED' else 'AMBIGUOUS',
            'recommended_action':reason}

def base(atom: dict, evidence: dict):
    condition=atom['condition']
    if atom['exception']: condition=(condition+'；' if condition else '')+'例外：'+atom['exception']
    sections=re.findall(r'\b\d{2} \d{2} \d{2}\b',evidence['raw_text'])
    return {'candidate_key':'C-'+key(evidence['evidence_id'],atom['candidate_key']),
            'requirement_status':'CONDITIONAL' if condition else 'CONFIRMED',
            'evidence_ids':atom['evidence_ids'],'inference_rule_id':None,'condition':condition,
            'entity_ids':[atom['subject']],'csi_sections':list(dict.fromkeys(sections)),
            'location':None,'field_evidence':{},
            'support_note':'文本抽取候选；待工程师逐项核验，尚未做独立语义证据验证。'}

def material(atom: dict,evidence: dict):
    c=base(atom,evidence)
    properties=deepcopy(atom['properties'])
    explicit_kind=next((p['value'].upper() for p in properties if p['name']=='material_kind'),None)
    temporary=explicit_kind=='TEMPORARY' or bool(re.search(r'formwork|shoring|temporary|模板|临时',atom['object'],re.I))
    if explicit_kind not in ('PERMANENT','TEMPORARY'):
        c['support_note']+=' 材料永久/临时类别暂由词规则建议。'
    c.update(name=atom['object'],material_kind='TEMPORARY' if temporary else 'PERMANENT',
             design_properties=[p for p in properties if p['name']!='material_kind'],
             option_relation='NONE',options=[],selected_option_ids=[],quantity=None,
             parent_entity_id=None,included_in_parent=False)
    c['field_evidence']['name']=atom['evidence_ids']
    return c

def inspection(atom: dict,evidence: dict):
    c=base(atom,evidence)
    props={p['name']:p for p in atom['properties']}
    qa=props.get('qa_type',{}).get('value')
    allowed=json.loads((ROOT/'spec/schemas/inspection-item.schema.json').read_text(encoding="utf-8"))['properties']['qa_type']['enum']
    if qa not in allowed:
        qa='TEST_REPORT' if atom['category']=='REPORT' else 'FIELD_TEST' if atom['category']=='TEST' else 'INITIAL_INSPECTION'
        c['support_note']+=' 检查子类型为初步归类，须核验。'
    c.update(qa_type=qa,activity=atom['subject'],requirement=atom['action']+' '+atom['object'],
             timing=None,frequency=None,acceptance_criteria=None,standard_reference=None,
             standard_body_available=False,report_name=atom['object'] if atom['category']=='REPORT' else None,
             submission_trigger=None,submission_offset_days=None,related_requirement_keys=[],
             performer_as_stated=None,witness_as_stated=None)
    for field in ('timing','frequency','acceptance_criteria','standard_reference','submission_trigger','report_name'):
        if field in props:
            c[field]=props[field]['value'];c['field_evidence'][field]=props[field]['evidence_ids']
    # 其他未映射属性不丢弃，保存供审核；完整结构化映射列入后续任务。
    extras=[p for p in atom['properties'] if p['name'] not in c]
    if extras:c['support_note']+=' 原子属性：'+ '; '.join(p['name']+'='+p['value'] for p in extras)
    return c

def envelopes(run: dict, evidence_records: dict, extracted: list[tuple[dict,dict]], parser_missing: list[dict]):
    scope=EvidenceScope('local',run['project_id'],run['snapshot_id'],evidence_records)
    candidates=[]; material_groups=defaultdict(list)
    for evidence,result in extracted:
        if result['disposition']!='CANDIDATES':
            candidates.append(('MISSING',missing(evidence['document_id'],result['reason'],'NOT_LOCATED',[evidence['evidence_id']],evidence['evidence_id'])))
        for atom in result['requirements']:
            if atom['needs_context'] or atom['parent_requirement_key'] or atom['option_relation']!='NONE':
                candidates.append(('MISSING',missing(atom['subject'],'当前要求涉及未完成的父条款/上下文/选项关联；原始要求已保留。','NOT_LOCATED',atom['evidence_ids'],atom['candidate_key'])))
                continue
            if atom['category']=='MATERIAL':
                mat=material(atom,evidence)
                # 只有显式设备Tag式主体可以尝试跨文档合并；普通名称不代表同一安装实例。
                tag=bool(re.fullmatch(r'[A-Za-z]{1,12}-\d+[A-Za-z0-9-]*',atom['subject']))
                group=(atom['subject'].casefold(),atom['object'].casefold(),mat['condition']) if tag else (evidence['evidence_id'],atom['candidate_key'])
                material_groups[group].append((mat,evidence))
            elif atom['category'] in ('INSPECTION','TEST','REPORT'):
                candidates.append(('INSPECTION',inspection(atom,evidence)))
            else:
                candidates.append(('MISSING',missing(atom['subject'],'原子要求已抽取，但尚无对应输出映射。','NOT_LOCATED',atom['evidence_ids'],atom['candidate_key'])))
    for group,items in material_groups.items():
        merged=deepcopy(items[0][0]); by_property=defaultdict(list)
        merged['evidence_ids']=list(dict.fromkeys(eid for mat,_ in items for eid in mat['evidence_ids']))
        merged['field_evidence']['name']=merged['evidence_ids']
        for mat,ev in items:
            for prop in mat['design_properties']:
                by_property[(prop['name'].casefold(),prop['unit'])].append((prop,ev))
        merged['design_properties']=[]
        for (propname,unit),values in by_property.items():
            distinct={v['value'] for v,_ in values}
            if len(distinct)==1:
                prop=deepcopy(values[0][0]);prop['evidence_ids']=list(dict.fromkeys(eid for p,_ in values for eid in p['evidence_ids']))
                merged['design_properties'].append(prop);continue
            compare='|'.join(map(str,group))+'|'+propname+'|'+str(unit)
            decision=select_latest([RevisionClaim(ev['evidence_id'],compare,p['value'],ev['internal_revision_date']) for p,ev in values])
            selected=decision.selected_evidence_ids[0] if decision.selected_evidence_ids else None
            if selected:
                merged['design_properties'].append(deepcopy(next(p for p,ev in values if ev['evidence_id']==selected)))
            else:
                merged['requirement_status']='CONFLICT'
                merged['design_properties'].extend(deepcopy(p) for p,_ in values)
            conflict={'candidate_key':'CF-'+key(compare),'subject':items[0][0]['name'],'comparison_key':compare,
                      'conflict_type':'DESIGN_PROPERTY','claims':[{'evidence_id':ev['evidence_id'],'normalized_value':p['value'],
                      'internal_revision_date':ev['internal_revision_date'],'revision_label':ev['revision_label'],
                      'scope_key':compare,'approval_text':None} for p,ev in values],
                      'resolution_status':decision.status,'selected_evidence_id':selected,'reason':decision.reason,
                      'affected_material_keys':[merged['candidate_key']],'affected_inspection_keys':[],
                      'severity_candidate':'UNRATED'}
            candidates.append(('CONFLICT',conflict))
        candidates.append(('MATERIAL',merged))
    candidates.extend(('MISSING',m) for m in parser_missing)
    for kind,candidate in candidates:
        rid='REC-'+key(run['id'],kind,candidate['candidate_key'])
        meta={'record_id':rid,'tenant_id':'local','project_id':run['project_id'],'analysis_run_id':run['id'],
              'input_snapshot_id':run['snapshot_id'],'created_at':now(),'app_version':VERSION,'spec_version':VERSION,
              'schema_version':SCHEMA_VERSION,'parser_version':'text-baseline-1','prompt_version':'0.2.0',
              'provider':run['provider'],'model_id':'mock-no-network' if run['provider']=='mock' else run.get('model','deepseek-v4-flash'),
              'model_snapshot':None,'routing_version':'0.2.0','retrieval_version':'exact-only-v1',
              'assembly_rule_version':'disabled-v1','revision_policy_version':'0.2.0','request_id':None}
        yield wrap_candidate(kind,candidate,meta,scope)
