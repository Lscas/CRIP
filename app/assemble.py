"""原子要求到可审核记录的基线转换。复杂实体、选项与视觉关联仍待实现。"""
from __future__ import annotations
import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from app.db import dumps, now
from app.parsers import PARSER_VERSION
from app.settings import VERSION, ROOT
from contracts.runtime_rules import (EvidenceScope,RevisionClaim,select_latest,wrap_candidate)

SCHEMA_VERSION='0.2.0'
TAG_PATTERN=r'[A-Za-z]{1,12}-\d+[A-Za-z0-9-]*'
SUPPORT_NOTE_MAX_LENGTH=400
SUPPORT_NOTE_TRUNCATION=' …[已截断；完整属性保留在抽取证据中]'
_ACTOR = re.compile(
    r'\b(?:owner|contractor|subcontractor|design[- ]builder|architect|engineer|installer|'
    r'manufacturer|supplier|testing agency|inspection agency|inspector|qc manager|'
    r'quality control manager|laboratory|fabricator|commissioning authority|balancing contractor)\b',
    re.I,
)
_MATERIAL_DESCRIPTOR = re.compile(
    r'^(?:(?:minimum\s+)?insulation thickness(?: per pipe size)?(?:\s+\S+\s*(?:inch(?:es)?|in\.?|mm))?|'
    r'piping(?: system)? insulation thickness|'
    r'water service pipe material and size|\d+(?:\.\d+)?\s*(?:gauge|awg)|'
    r'(?:minimum|maximum)?\s*(?:thickness|size|diameter|pressure|temperature|voltage|flow rate)|'
    r'pipe type|control joint spacing|(?:suspended )?rod diameter specification table|'
    r'ul system no\.?|ansi\s+[a-z0-9.-]+)$',
    re.I,
)
_NON_MATERIAL = re.compile(
    r'\b(?:shop drawings?|submittals?|table of contents|schedule of values|record documents?|'
    r'operation and maintenance|o\s*&\s*m\b|manuals?|warrant(?:y|ies)|schematic|diagrams?|'
    r'transmittals?|calculations?|reports?|certificates?|instructions?|spare parts?|extra materials?)\b',
    re.I,
)
_QA_ACTION = re.compile(
    r'\b(?:inspect(?:ed|ing|ion)?|test(?:ed|ing)?|check(?:ed|ing)?|verify|verification|witness|hold point|'
    r'perform|conduct|examine|observe|measure|commission(?:ing)?|start[- ]?up|balance|balancing|calibrat(?:e|ion)|functional performance|'
    r'hydrostatic|pressure test|leak(?:age)? test|continuity|megger|torque test|'
    r'bacteriological|airflow|sound level|vibration)\b',
    re.I,
)
_NON_QA_DOCUMENT = re.compile(
    r'\b(?:shop drawings?|product data|submittal register|schedule of values|table of contents|'
    r'transmittals?|record documents?|schematic|wiring diagrams?|coordination drawings?|'
    r'operation and maintenance|o\s*&\s*m\b|manuals?|warrant(?:y|ies)|cost|bid|'
    r'time impact analysis|schedule extension|total float|critical path|unstamped submittals?)\b',
    re.I,
)
_ADMINISTRATIVE_VERIFY = re.compile(
    r'\bverify\b.{0,80}\b(?:this information|dimensions?|relationships?|existing equipment sizes?|interferences?|'
    r'contract documents?|routing|quantities submitted)\b|\bbase\b.{0,100}\btest data\b',
    re.I,
)
_NON_QA_REQUIREMENT = re.compile(
    r'^\s*(?:include all test results|complete all applicable tests|coordinate\b|refer to applicable code|'
    r'include in the punch list|visit the premises|review and document attendance|audit and approve)|'
    r'\b(?:applicable tests, certifications, forms, and matrices|test plan(?: summary)?|'
    r'must be installed prior to testing|tests? that fail.{0,80}remedied|'
    r'attachment points.{0,80}written certification|locations? of wall-mounted devices.{0,80}drawings)\b',
    re.I,
)
_DESCRIPTIVE_MATERIAL_PHRASE = re.compile(
    r'\b(?:including|constructed from|made of|classified by|listed by|designed for use|'
    r'flame-spread|smoke-developed|receive insulation thickness|sizes? up to)\b',
    re.I,
)
_ATTRIBUTE_ONLY_MATERIAL = re.compile(
    r'^(?:all new materials?\b|all additional hardware\b|all documentation\b|products? that\b|'
    r'materials? and systems?\b|state fire marshal approved\b|wet location listed\b|'
    r'(?:efficiencies|standards?) listed\b|listed (?:category|standards?)\b|'
    r'low emitting material limits\b|operating temperature range|maximum (?:ambient temperature|input voltage)|'
    r'design pressure and temperature|mppt operating voltage range|temperature rise|dc voltage ripple|'
    r'to give a control voltage\b|with \d+(?:\.\d+)?\s*lb\b|\d+(?:\.\d+)?\s*psi\b|'
    r'minimum of \d+(?:\.\d+)?\s*gauge\b)',
    re.I,
)
_GENERIC_QA_OBJECT = re.compile(r'^(?:equipment|system|work|installation|materials?|testing|inspection|test|report)$',re.I)
_ENTITY_PROPERTY = {'material_name','equipment_name','product_name','item_name','test_name','inspection_name'}
QUALITY_REPAIR_NOTE=('Deterministic quality checks removed invalid material or QA items, duplicate or out-of-scope '
                     'properties, or orphaned parent links. Review the source extraction.')

def key(*values):return hashlib.sha256(dumps(values).encode()).hexdigest()[:24]

def _property(atom: dict, *names: str) -> dict | None:
    wanted={re.sub(r'[^a-z0-9]+','_',name.casefold()).strip('_') for name in names}
    for prop in atom.get('properties') or []:
        normalized=re.sub(r'[^a-z0-9]+','_',str(prop.get('name','')).casefold()).strip('_')
        if normalized in wanted:return prop
    return None

def _is_actor(value: str | None) -> bool:
    return bool(value and _ACTOR.search(value))

def _title_if_upper(value: str) -> str:
    return value.title() if value and value.upper()==value else value

def _material_name(atom: dict) -> str:
    """Choose the installed object, never a property heading, as the item name."""
    name=str(atom.get('object') or '').strip()
    subject=str(atom.get('subject') or '').strip()
    if re.fullmatch(r'water service pipe material and size',name,re.I):
        service=_property(atom,'type','service type')
        if service:
            return _title_if_upper(str(service['value']).strip())+' Pipe'
    if (_MATERIAL_DESCRIPTOR.fullmatch(name) or name.casefold() in {'equipment','material','materials','product','products'}
            or len(name)>max(64,len(subject)+28) or _DESCRIPTIVE_MATERIAL_PHRASE.search(name)):
        if subject and not _is_actor(subject):return subject
    if _is_actor(subject):
        name=re.sub(r'^complete\s+(.+?)\s+including\b.*$',r'\1',name,flags=re.I)
        name=re.sub(r'^seismic restraint and equipment\b.*$','Seismic Restraints',name,flags=re.I)
    return name

def _material_atom_allowed(atom: dict) -> bool:
    name=_material_name(atom)
    if not name or _is_actor(name):return False
    if _NON_MATERIAL.search(name):return False
    if re.search(r'\bsoftware\b',name,re.I):return False
    if name.casefold() in {'equipment','material','materials','product','products','item','items'}:return False
    if re.fullmatch(r'(?:astm|ansi|nfpa|ul|ashrae|iecc|scaqmd)\b.*',name,re.I):return False
    if _ATTRIBUTE_ONLY_MATERIAL.search(name):return False
    return not _MATERIAL_DESCRIPTOR.fullmatch(name)

def _inspection_atom_allowed(atom: dict) -> bool:
    text=' '.join(str(atom.get(name) or '') for name in ('action','object','subject'))
    if not _QA_ACTION.search(text):return False
    if _NON_QA_DOCUMENT.search(text) or _ADMINISTRATIVE_VERIFY.search(text):return False
    if _NON_QA_REQUIREMENT.search(text):return False
    if _GENERIC_QA_OBJECT.fullmatch(str(atom.get('object') or '').strip()):return False
    category=atom.get('category');action=str(atom.get('action') or '')
    if category in ('INSPECTION','TEST') and not _QA_ACTION.search(action):return False
    if category=='REPORT' and not re.search(
            r'\b(?:test|inspection|start[- ]?up|commissioning|balancing|quality control)\b.{0,50}\breport\b|'
            r'\breport\b.{0,50}\b(?:test|inspection|start[- ]?up|commissioning|balancing|quality control)\b',text,re.I):
        return False
    return True

def _evidence_is_local(left: dict, right: dict) -> bool:
    if left.get('document_id')!=right.get('document_id'):return False
    a=left.get('locator') or {};b=right.get('locator') or {}
    if a.get('section') and b.get('section'):return a.get('section')==b.get('section')
    ap=a.get('page_number');bp=b.get('page_number')
    if type(ap) is int and type(bp) is int:return abs(ap-bp)<=1
    ae=a.get('text_line_end');bs=b.get('text_line_start')
    if type(ae) is int and type(bs) is int:return abs(bs-ae)<=1
    return a.get('native_element_id') is not None and a.get('native_element_id')==b.get('native_element_id')


def _workflow_source_role(evidence: dict) -> str | None:
    section=str((evidence.get('locator') or {}).get('section') or '').upper()
    if 'RFI' not in section:return None
    if '> RESPONSE' in section:return 'RFI_RESPONSE'
    if '> QUESTION' in section or '> MIXED' in section:return 'RFI_QUESTION'
    return 'RFI_UNKNOWN'


def _rejected_submittal_source(evidence: dict) -> bool:
    section=str((evidence.get('locator') or {}).get('section') or '').upper()
    return 'SUBMITTAL' in section and ('STATUS: REJECTED' in section or
                                       'STATUS: REVISE AND RESUBMIT' in section)


def _email_header_source(evidence: dict) -> bool:
    section=str((evidence.get('locator') or {}).get('section') or '').upper()
    return section.startswith('EMAIL > HEADERS')


def _email_quoted_history_source(evidence: dict) -> bool:
    section=str((evidence.get('locator') or {}).get('section') or '').upper()
    return section.startswith('EMAIL > QUOTED HISTORY')


def _ineligible_workflow_evidence(evidence: dict, *, block_rfi_question: bool) -> str | None:
    if _email_quoted_history_source(evidence):return 'EMAIL_QUOTED_HISTORY'
    if _email_header_source(evidence):return 'EMAIL_HEADER'
    if _rejected_submittal_source(evidence):return 'REJECTED_SUBMITTAL'
    if block_rfi_question and _workflow_source_role(evidence) in {'RFI_QUESTION','RFI_UNKNOWN'}:
        return 'RFI_QUESTION'
    return None


def _filter_workflow_evidence(evidence_ids: list[str], records: dict[str,dict], suffix: str,
                              *, block_rfi_question: bool) -> tuple[list[str],list[str]]:
    kept=[];flags=[]
    for evidence_id in evidence_ids:
        reason=(_ineligible_workflow_evidence(records[evidence_id],block_rfi_question=block_rfi_question)
                if evidence_id in records else None)
        if reason:flags.append(reason+'_'+suffix)
        else:kept.append(evidence_id)
    return kept,flags


def apply_deterministic_quality(data: dict, evidence_records: list[dict] | dict[str,dict] | None = None,
                                ) -> tuple[dict,list[str]]:
    """Remove only structurally clear false positives; never invent or reassign facts."""
    records=({item['evidence_id']:item for item in evidence_records} if isinstance(evidence_records,list)
             else evidence_records or {})
    checked=deepcopy(data);kept=[];flags=[]
    for atom in checked.get('requirements',[]):
        category=atom.get('category')
        target=category in ('MATERIAL','INSPECTION','TEST','REPORT')
        if target:
            evidence_ids,rejected=_filter_workflow_evidence(
                atom.get('evidence_ids') or [],records,'SOURCE',block_rfi_question=True)
            if rejected:
                flags.extend(rejected);atom['evidence_ids']=evidence_ids
                if not evidence_ids:continue
        if category=='MATERIAL' and not _material_atom_allowed(atom):
            flags.append('MATERIAL_NAME');continue
        if category in ('INSPECTION','TEST','REPORT') and not _inspection_atom_allowed(atom):
            flags.append('QA_ACTIVITY');continue
        scope=set(atom.get('evidence_ids') or [])|set(atom.get('context_evidence_ids') or [])
        properties=[];seen=set()
        for prop in atom.get('properties') or []:
            normalized=re.sub(r'[^a-z0-9]+','_',str(prop.get('name','')).casefold()).strip('_')
            prop_evidence,rejected=_filter_workflow_evidence(
                prop.get('evidence_ids') or [],records,'PROPERTY',block_rfi_question=target)
            if rejected:
                flags.extend(rejected);prop['evidence_ids']=prop_evidence
                if not prop_evidence:continue
            prop_ids=set(prop_evidence)
            if not prop_ids.issubset(scope):
                flags.append('PROPERTY_SCOPE');continue
            primary_ids=set(atom.get('evidence_ids') or [])
            if records and not prop_ids.intersection(primary_ids):
                related=any(_evidence_is_local(records[left],records[right])
                            for left in primary_ids if left in records
                            for right in prop_ids if right in records)
                if not related:
                    flags.append('PROPERTY_RELATION');continue
            if category=='MATERIAL' and normalized in _ENTITY_PROPERTY:
                flags.append('PROPERTY_ROLE');continue
            identity=(normalized,str(prop.get('value')),prop.get('unit'),tuple(sorted(prop.get('evidence_ids') or [])))
            if identity in seen:
                flags.append('DUPLICATE_PROPERTY');continue
            seen.add(identity);properties.append(prop)
        atom['properties']=properties;kept.append(atom)
    keys={atom['candidate_key'] for atom in kept}
    for atom in kept:
        if atom.get('parent_requirement_key') and atom['parent_requirement_key'] not in keys:
            atom['parent_requirement_key']=None;atom['needs_context']=True;flags.append('PARENT_SCOPE')
    checked['requirements']=kept
    if flags:
        checked['disposition']='TRUNCATED'
        reason=str(checked.get('reason') or '').strip()
        available=max(0,400-len(QUALITY_REPAIR_NOTE)-1)
        checked['reason']=(reason[:available]+' ' if reason[:available] else '')+QUALITY_REPAIR_NOTE
    return checked,sorted(set(flags))

def bounded_support_note(value: str) -> str:
    """Keep generated review notes within the candidate contract.

    The complete model extraction remains in ``evidence.extraction``; this
    field is only a compact reviewer-facing summary.
    """
    if len(value)<=SUPPORT_NOTE_MAX_LENGTH:return value
    keep=SUPPORT_NOTE_MAX_LENGTH-len(SUPPORT_NOTE_TRUNCATION)
    return value[:keep].rstrip()+SUPPORT_NOTE_TRUNCATION

def material_group_key(candidate: dict) -> str | None:
    """Return the stable identity for an eligible tagged material candidate."""
    entities=candidate.get('entity_ids') or []
    subject=entities[0] if len(entities)==1 and isinstance(entities[0],str) else None
    name=candidate.get('name')
    if not subject or not isinstance(name,str) or not re.fullmatch(TAG_PATTERN,subject):return None
    return 'MG-'+key('TAG',subject.casefold(),name.casefold(),candidate.get('condition'))

def missing(subject: str, reason: str, code: str, evidence_ids=None, target=None):
    return {'candidate_key':'MI-'+key(subject,reason),'subject':subject,
            'missing_field_or_document':reason,'blocking_reason':'当前内容审查无法完成，需要处理后重新分析。',
            'blocks_current_task':True,'reason_code':code,'related_evidence_ids':evidence_ids or [],
            'affected_output_keys':[target or subject],'search_state':'PARSE_UNAVAILABLE' if code=='PARSE_FAILED' else 'AMBIGUOUS',
            'recommended_action':reason}

def base(atom: dict, evidence: dict, sources: list[dict] | None = None):
    sources=sources or [evidence]
    condition=atom['condition']
    if atom['exception']: condition=(condition+'；' if condition else '')+'例外：'+atom['exception']
    source_sections=[str((source.get('locator') or {}).get('section') or '') for source in sources]
    sections=list(dict.fromkeys(section for source,locator in zip(sources,source_sections)
                                for section in re.findall(r'\b\d{2} \d{2} \d{2}\b',
                                                          str(source.get('raw_text') or '')+'\n'+locator)))
    status=[];submittal_without_status=False
    for locator in source_sections:
        if 'SUBMITTAL' not in locator.upper():continue
        match=re.search(r'STATUS:\s*([^>]+)',locator,re.I)
        if match:status.append(match.group(1).strip())
        else:submittal_without_status=True
    status=list(dict.fromkeys(status))
    source_conditions=[];source_notes=[]
    if status or submittal_without_status:
        if len(status)==1 and not submittal_without_status:
            source_conditions.append('Submittal status: '+status[0]+'.')
        elif status:
            source_conditions.append('Submittal statuses: '+'; '.join(status+(['NOT ESTABLISHED'] if submittal_without_status else []))+'.')
        else:source_conditions.append('Submittal source; approval status is not established.')
        source_notes.append('Submittal-source candidate; it does not replace the design requirement without review.')
    if any(_workflow_source_role(source)=='RFI_RESPONSE' for source in sources):
        source_conditions.append('RFI response source; contractual effect requires review.')
        source_notes.append('RFI-response candidate; the question itself is not treated as a revision directive.')
    if any(str((source.get('locator') or {}).get('section') or '').upper().startswith('EMAIL > BODY')
           for source in sources):
        source_conditions.append('Email source; authority and contractual effect require review.')
        source_notes.append('Email-source candidate; attachments are outside this evidence unless separately analyzed.')
    if source_conditions:condition=(condition+'; ' if condition else '')+'; '.join(source_conditions)
    source_note=''.join(' '+note for note in source_notes)
    return {'candidate_key':'C-'+key(evidence['evidence_id'],atom['candidate_key']),
            'requirement_status':'CONDITIONAL' if condition else 'CONFIRMED',
            'evidence_ids':atom['evidence_ids'],'inference_rule_id':None,'condition':condition,
            'entity_ids':[atom['subject']],'csi_sections':list(dict.fromkeys(sections)),
            'location':None,'field_evidence':{},
            'support_note':'Text-extraction candidate; engineer review and independent source verification are required.'+source_note}

def material(atom: dict,evidence: dict,sources: list[dict] | None = None):
    c=base(atom,evidence,sources)
    properties=deepcopy(atom['properties'])
    explicit_kind=next((p['value'].upper() for p in properties if p['name']=='material_kind'),None)
    temporary=explicit_kind=='TEMPORARY' or bool(re.search(r'formwork|shoring|temporary|模板|临时',atom['object'],re.I))
    if explicit_kind not in ('PERMANENT','TEMPORARY'):
        c['support_note']+=' 材料永久/临时类别暂由词规则建议。'
    sections=list(c['csi_sections'])
    design_properties=[];quantity=None
    for prop in properties:
        normalized=re.sub(r'[^a-z0-9]+','_',str(prop.get('name','')).casefold()).strip('_')
        if normalized=='material_kind':continue
        if normalized in {'specification_section','spec_section','csi_section'}:
            sections.extend(re.findall(r'\b\d{2} \d{2} \d{2}\b',str(prop.get('value',''))));continue
        if (quantity is None and re.search(r'(?:^|_)(?:quantity|count|number|total)(?:_|$)',normalized)
                and re.fullmatch(r'\d+(?:\.\d+)?',str(prop.get('value','')).strip())):
            raw=str(prop['value']).strip();value=float(raw) if '.' in raw else int(raw)
            quantity={'value':value,'unit':prop.get('unit') or 'EA','basis':'DESIGN_NET',
                      'method':'EXPLICIT_DOCUMENT','scope_key':atom['candidate_key'],
                      'entity_ids':[atom['subject']],'evidence_ids':prop['evidence_ids'],
                      'formula':None,'operands':[],'calibration':None}
            continue
        design_properties.append(prop)
    c.update(name=_material_name(atom),material_kind='TEMPORARY' if temporary else 'PERMANENT',
             csi_sections=list(dict.fromkeys(sections)),design_properties=design_properties,
             option_relation='NONE',options=[],selected_option_ids=[],quantity=quantity,
             parent_entity_id=None,included_in_parent=False)
    if re.fullmatch(r'water service pipe material and size',str(atom.get('object') or ''),re.I):
        match=re.match(r'(?P<tag>W\d+)\s+at\s+(?P<address>.+?)\s+combined\b',str(atom.get('subject') or ''),re.I)
        if match:c['location']=f"{match.group('tag')} at {match.group('address')}"
    c['field_evidence']['name']=atom['evidence_ids']
    return c

def inspection(atom: dict,evidence: dict,sources: list[dict] | None = None):
    c=base(atom,evidence,sources)
    props={p['name']:p for p in atom['properties']}
    qa=props.get('qa_type',{}).get('value')
    allowed=json.loads((ROOT/'spec/schemas/inspection-item.schema.json').read_text(encoding="utf-8"))['properties']['qa_type']['enum']
    if qa not in allowed:
        qa='TEST_REPORT' if atom['category']=='REPORT' else 'FIELD_TEST' if atom['category']=='TEST' else 'INITIAL_INSPECTION'
        c['support_note']+=' 检查子类型为初步归类，须核验。'
    c['field_evidence']['requirement']=atom['evidence_ids']
    c['field_evidence']['activity']=atom['evidence_ids']
    stated_performer=str(atom['subject']).strip() if _is_actor(str(atom['subject'])) else None
    c.update(qa_type=qa,activity=atom['action'],requirement=atom['object'],
             timing=None,frequency=None,acceptance_criteria=None,standard_reference=None,
             standard_body_available=False,report_name=atom['object'] if atom['category']=='REPORT' else None,
             submission_trigger=None,submission_offset_days=None,related_requirement_keys=[],
             performer_as_stated=stated_performer,witness_as_stated=None)
    for field in ('timing','frequency','acceptance_criteria','standard_reference','submission_trigger','report_name',
                  'performer_as_stated','witness_as_stated'):
        if field in props:
            c[field]=props[field]['value'];c['field_evidence'][field]=props[field]['evidence_ids']
    section_prop=next((props[name] for name in ('specification_section','spec_section','csi_section') if name in props),None)
    if section_prop:
        c['csi_sections']=list(dict.fromkeys(c['csi_sections']+re.findall(r'\b\d{2} \d{2} \d{2}\b',section_prop['value'])))
    # 其他未映射属性不丢弃，保存供审核；完整结构化映射列入后续任务。
    extras=[p for p in atom['properties'] if p['name'] not in c]
    if extras:c['support_note']+=' 原子属性：'+ '; '.join(p['name']+'='+p['value'] for p in extras)
    return c

def envelopes(run: dict, evidence_records: dict, extracted: list[tuple[dict,dict]], parser_missing: list[dict]):
    scope=EvidenceScope('local',run['project_id'],run['snapshot_id'],evidence_records)
    candidates=[]; material_groups=defaultdict(list)
    for evidence,result in extracted:
        for atom in result['requirements']:
            if atom['needs_context'] or atom['parent_requirement_key'] or atom['option_relation']!='NONE':
                continue
            sources=[evidence_records[evidence_id] for evidence_id in atom['evidence_ids']
                     if evidence_id in evidence_records]
            source=sources[0] if sources else evidence
            if atom['category']=='MATERIAL' and _material_atom_allowed(atom):
                mat=material(atom,source,sources)
                # 只有显式设备Tag式主体可以尝试跨文档合并；普通名称不代表同一安装实例。
                tag=bool(re.fullmatch(TAG_PATTERN,atom['subject']))
                group=('TAG',atom['subject'].casefold(),atom['object'].casefold(),mat['condition']) if tag else ('EVIDENCE',source['evidence_id'],atom['candidate_key'])
                material_groups[group].append((mat,source))
            elif atom['category'] in ('INSPECTION','TEST','REPORT') and _inspection_atom_allowed(atom):
                candidates.append(('INSPECTION',inspection(atom,source,sources)))
    for group,items in material_groups.items():
        merged=deepcopy(items[0][0]); by_property=defaultdict(list)
        # A tagged cross-evidence material is one logical record.  Derive its
        # identity from the stable group, not whichever evidence happens to
        # sort first on a partial publish/resume cycle.
        if group[0]=='TAG':
            merged['candidate_key']=material_group_key(merged)
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
                merged['requirement_status']='NOT_SPECIFIED'
                seen_values=set()
                for prop,_ in values:
                    identity=(prop['value'],prop.get('unit'))
                    if identity not in seen_values:
                        merged['design_properties'].append(deepcopy(prop));seen_values.add(identity)
        candidates.append(('MATERIAL',merged))
    for kind,candidate in candidates:
        if isinstance(candidate.get('support_note'),str):
            candidate['support_note']=bounded_support_note(candidate['support_note'])
        rid='REC-'+key(run['id'],kind,candidate['candidate_key'])
        meta={'record_id':rid,'tenant_id':'local','project_id':run['project_id'],'analysis_run_id':run['id'],
              'input_snapshot_id':run['snapshot_id'],'created_at':now(),'app_version':VERSION,'spec_version':VERSION,
              'schema_version':SCHEMA_VERSION,'parser_version':PARSER_VERSION,'prompt_version':'0.2.0',
              'provider':run['provider'],'model_id':'mock-no-network' if run['provider']=='mock' else run['model'],
              'model_snapshot':None,'routing_version':'0.2.0','retrieval_version':'exact-only-v1',
              'assembly_rule_version':'disabled-v1','revision_policy_version':'0.2.0','request_id':None}
        yield wrap_candidate(kind,candidate,meta,scope)
