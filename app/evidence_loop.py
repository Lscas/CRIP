"""Independent, bounded evidence acquisition loop for project QA V3."""
from __future__ import annotations

import hashlib
import re

from app.db import Database,DomainError,dumps
from app.gateway import InvalidModelOutput
from app.page_selector import (PageSelection,COMPLETE_SELECTOR_VERSION as SELECTOR_VERSION,
                               COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION,
                               COMPLETE_SELECTOR_VERSIONS,select_pages)
from app.project_qa_v2 import (_calculation_value,_decimal_text,_enrich,
                               validate_answer_v2)
from app.answer_diagnostics import DecisionContractError
from app.visual_pipeline import render_visual_png
from contracts.runtime_rules import validate_schema

MAX_MODEL_DECISIONS=3
MAX_EVIDENCE_REQUESTS=2
INITIAL_CONTEXT_BYTES=18_000
MAX_CONTEXT_BYTES=18_000
MAX_LAYOUT_CONTEXT_BYTES=6_000
MAX_VISUAL_PAGES=3
MAX_VISUAL_BYTES=48*1024*1024
_EXPLICIT_IDENTIFIER=re.compile(
    r'(?i)\b(?:SHEET|DRAWING|DWG\.?|SECTION|PARAGRAPH|PARA\.?|CLAUSE|ARTICLE|RFI|SUBMITTAL)'
    r'\s*(?:NO\.?\s*)?[:#-]?\s*[A-Z0-9][A-Z0-9._/ -]{0,31}')
_PARALLEL_QUESTION=re.compile(
    r'(?i),\s+and\s+(?=(?:what|how|which|does|do|state|whether)\b)')
_LEADING_SOURCE_SCOPE=re.compile(
    r'(?i)^(?:on|for|in|using)\b[^,]{1,160},\s*'
    r'(?=(?:what|which|how|does|do)\b)')


def _answer_parts(question:str)->list[dict]:
    """Split only explicit question lists/parallel clauses into audit parts."""
    compact=' '.join(question.split()).strip()
    if not compact:return [{'part_ref':'P1','text':'The requested answer.'}]
    text=compact.rstrip(' ?')
    clauses=_PARALLEL_QUESTION.split(text)
    parts=[]
    for clause in clauses:
        body=_LEADING_SOURCE_SCOPE.sub('',clause.strip())
        if ':' in body:
            prefix,tail=body.split(':',1)
            if ',' in tail:body=tail.strip()
        match=re.search(r'(?i),\s+and\s+',body)
        if match and body[:match.start()].count(',')>=1:
            parts.extend(item.strip(' ,') for item in (
                body[:match.start()].split(',')+[body[match.end():]]) if item.strip(' ,'))
        else:parts.append(body.strip(' ,'))
    parts=[item for item in parts if item]
    if len(parts)<2 or len(parts)>12:parts=[text]
    return [{'part_ref':f'P{index}','text':item}
            for index,item in enumerate(parts,1)]


def _request_key(request:dict)->tuple[str,str]:
    return request['tool'],' '.join(request['query'].split()).casefold()


def _fit_rows(rows:list[dict],limit:int|None,layout_limit:int=MAX_LAYOUT_CONTEXT_BYTES)->list[dict]:
    """Keep the existing source budget, then add bounded local layout context."""
    result=[];seen=set();used=2
    if limit is None:
        for row in rows:
            evidence_id=row.get('evidence_id');text=row.get('raw_text')
            if not isinstance(evidence_id,str) or not evidence_id or evidence_id in seen:continue
            if not isinstance(text,str) or not text:continue
            result.append({**row,'prompt_text':text});seen.add(evidence_id)
        return result
    for row in rows:
        evidence_id=row.get('evidence_id')
        if not isinstance(evidence_id,str) or not evidence_id or evidence_id in seen:continue
        text=row.get('prompt_text',row.get('raw_text',''))
        if not isinstance(text,str) or not text:continue
        separator=1 if result else 0;remaining=limit-used-separator
        if remaining<=0:break
        def prompt_item(value:str)->dict:
            return {
                'evidence_id':evidence_id,'file_name':row.get('file_name'),
                'locator':row.get('locator'),'text':value,
            }
        source_size=len(dumps(prompt_item(text)).encode('utf-8'))
        if source_size<=remaining:
            prompt_text=text;item_size=source_size
        else:
            low=0;high=len(text)
            while low<high:
                middle=(low+high+1)//2
                if len(dumps(prompt_item(text[:middle])).encode('utf-8'))<=remaining:
                    low=middle
                else:high=middle-1
            if low==0:break
            prompt_text=text[:low]
            item_size=len(dumps(prompt_item(prompt_text)).encode('utf-8'))
        fitted={**row,'prompt_text':prompt_text};fitted.pop('layout_lines',None)
        result.append(fitted);seen.add(evidence_id)
        used+=separator+item_size
        if prompt_text!=text:break
    if type(layout_limit) is not int or layout_limit<0:raise ValueError('layout limit is invalid')
    if layout_limit==0:return result
    ceiling=limit+layout_limit
    by_id={row.get('evidence_id'):row for row in rows}
    def serialized_size()->int:
        return len(dumps([{
            'evidence_id':item['evidence_id'],'file_name':item.get('file_name'),
            'locator':item.get('locator'),'text':item['prompt_text'],
            **({'layout_lines':item['layout_lines']} if item.get('layout_lines') else {}),
        } for item in result]).encode('utf-8'))
    for item in result:
        layout=by_id.get(item['evidence_id'],{}).get('layout_lines')
        if not isinstance(layout,str) or not layout:continue
        kept=[]
        for line in layout.splitlines():
            item['layout_lines']='\n'.join([*kept,line])
            if serialized_size()>ceiling:break
            kept.append(line)
        if kept:item['layout_lines']='\n'.join(kept)
        else:item.pop('layout_lines',None)
        if serialized_size()>=ceiling:break
    return result


def _compile_citation(citation:dict,evidence:dict[str,dict],
                      regions:dict[str,dict])->dict:
    """Resolve one provider reference only from the exact context it received."""
    if citation['type']=='TEXT':
        source=evidence.get(citation['evidence_ref'])
        if source is None:
            raise DecisionContractError('QA V3 citation is outside the evidence bundle',
                                        'citation_reference_outside')
        quote=source.get('prompt_text',source.get('raw_text'))
        if not isinstance(quote,str) or not quote:
            raise DecisionContractError('QA V3 citation source text is unavailable',
                                        'citation_source_unavailable')
        return {'type':'TEXT','evidence_id':source['evidence_id'],'quote':quote}
    region=regions.get(citation['region_ref'])
    if region is None:
        raise DecisionContractError('QA V3 image citation is outside the supplied visual regions',
                                    'citation_region_outside')
    return {
        'type':'IMAGE_REGION','region_id':region['region_id'],
        'document_id':region['document_id'],'page_number':region['page_number'],
        'bbox':region['bbox'],'observation':citation['observation'],'needs_review':True,
    }


def _compile_answer(answer:dict,evidence_rows:list[dict],
                     visual_regions:list[dict]|None=None)->dict:
    """Compile provider references into the unchanged public QA V2 answer."""
    evidence={f'E{index}':item for index,item in enumerate(evidence_rows,1)}
    regions={f'V{index}':item for index,item in enumerate(visual_regions or [],1)}
    claims=[{
        'text':item['text'],
        'citations':[_compile_citation(value,evidence,regions)
                     for value in item['citations']],
    } for item in answer['claims']]
    calculations=[]
    symbols={'ADD':'+','SUBTRACT':'-','MULTIPLY':'×','DIVIDE':'÷'}
    for item in answer['calculations']:
        citations=[_compile_citation(value,evidence,regions)
                   for value in item['citations']]
        operand_groups=([item['operands'][:1]+[value] for value in item['operands'][1:]]
                        if item['operator']=='PERCENT_OF' else [item['operands']])
        for operands in operand_groups:
            result=_decimal_text(_calculation_value(item['operator'],operands))
            calculation={'operator':item['operator'],'operands':operands,
                         'result':result,'citations':citations}
            calculations.append(calculation)
            if item['operator']=='PERCENT_OF':
                expression=f"{operands[0]} × {operands[1]}%"
            else:expression=f" {symbols[item['operator']]} ".join(operands)
            claims.append({'text':f'{expression} = {result}.','citations':citations})
    if not claims:
        raise DecisionContractError('QA V3 ANSWER requires at least one claim or calculation',
                                    'answer_empty')
    if len(claims)>12:
        raise DecisionContractError('QA V3 compiled answer has too many atomic claims',
                                    'answer_atomic_claim_limit')
    return {
        'status':'ANSWERED','answer':' '.join(item['text'] for item in claims),
        'claims':claims,'missing':[],'calculations':calculations,
    }


def _validate_answer_coverage(answer:dict,answer_parts:list[dict])->None:
    """Require every requested part and every emitted item to map explicitly."""
    coverage=answer['coverage'];expected=[item['part_ref'] for item in answer_parts]
    actual=[item['part_ref'] for item in coverage]
    if set(actual)!=set(expected):
        raise DecisionContractError('QA V3 ANSWER coverage is incomplete',
                                    'answer_part_missing')
    claim_count=len(answer['claims']);calculation_count=len(answer['calculations'])
    used_claims=set();used_calculations=set()
    for item in coverage:
        claim_indexes=item['claim_indexes']
        calculation_indexes=item['calculation_indexes']
        if not claim_indexes and not calculation_indexes:
            raise DecisionContractError('QA V3 ANSWER coverage has an empty part mapping',
                                        'answer_part_empty_mapping')
        if (len(claim_indexes)!=len(set(claim_indexes))
                or len(calculation_indexes)!=len(set(calculation_indexes))):
            raise DecisionContractError('QA V3 ANSWER coverage repeats an answer index',
                                        'answer_part_duplicate_index')
        if any(index>=claim_count for index in claim_indexes):
            raise DecisionContractError('QA V3 ANSWER coverage has an invalid claim index',
                                        'answer_part_invalid_claim_index')
        if any(index>=calculation_count for index in calculation_indexes):
            raise DecisionContractError('QA V3 ANSWER coverage has an invalid calculation index',
                                        'answer_part_invalid_calculation_index')
        used_claims.update(claim_indexes);used_calculations.update(calculation_indexes)
    if used_claims!=set(range(claim_count)):
        raise DecisionContractError('QA V3 ANSWER contains unmapped answer content',
                                    'answer_part_unmapped_claim')
    if used_calculations!=set(range(calculation_count)):
        raise DecisionContractError('QA V3 ANSWER contains unmapped answer content',
                                    'answer_part_unmapped_calculation')


def validate_evidence_decision(data:dict,evidence_rows:list[dict],question:str,
                               request_history:list[dict]|None=None,
                               visual_regions:list[dict]|None=None,
                               answer_parts:list[dict]|None=None,*,complete_context:bool=False)->dict:
    """Validate one model decision without accepting free-form reasoning or tools."""
    validate_schema('project-evidence-decision-complete' if complete_context
                    else 'project-evidence-decision',data)
    history={_request_key(item) for item in request_history or []}
    status=data['status'];reason=data['reason_code'];missing=data['missing_facts']
    requests=data['requests'];answer=data['answer']
    answer_parts=list(answer_parts or _answer_parts(question))
    if len(requests)>MAX_EVIDENCE_REQUESTS:
        raise ValueError('QA V3 requested too many evidence operations')
    if status=='ANSWER':
        if reason!='ENOUGH_EVIDENCE' or missing or requests or not isinstance(answer,dict):
            raise ValueError('QA V3 ANSWER decision is inconsistent')
        _validate_answer_coverage(answer,answer_parts)
        normalized={**data,'answer':_compile_answer(answer,evidence_rows,visual_regions)}
        validate_answer_v2(normalized['answer'],evidence_rows,question,visual_regions)
        return normalized
    if answer['claims'] or answer['calculations'] or answer['coverage']:
        raise ValueError('QA V3 non-answer decision cannot include answer content')
    if not missing:
        missing=['The supplied project evidence did not establish the requested facts.']
        data={**data,'missing_facts':missing}
    if status=='NEED_EVIDENCE':
        if reason not in ('MISSING_SOURCE_TEXT','MISSING_IDENTIFIER') or not requests:
            raise ValueError('QA V3 NEED_EVIDENCE decision is inconsistent')
        current=set()
        for request in requests:
            key=_request_key(request)
            if key in history or key in current:
                raise ValueError('QA V3 evidence request is duplicated')
            current.add(key)
            if request['tool']=='FIND_IDENTIFIER' and not _EXPLICIT_IDENTIFIER.search(request['query']):
                raise ValueError('QA V3 FIND_IDENTIFIER query has no supported explicit identifier')
        return data
    if requests:raise ValueError('QA V3 terminal non-answer decision cannot request evidence')
    if status=='NEED_USER_INPUT' and reason!='MISSING_PROJECT_FILE':
        raise ValueError('QA V3 NEED_USER_INPUT decision is inconsistent')
    if status=='CANNOT_ANSWER' and reason not in (
            'CONFLICTING_SOURCES','UNSUPPORTED_TASK','NO_NEW_EVIDENCE'):
        raise ValueError('QA V3 CANNOT_ANSWER decision is inconsistent')
    return data


def _supplement(db:Database,run:dict,requests:list[dict],current:list[dict],
                selector_version:str,scope_question:str
                )->tuple[list[dict],int,list[PageSelection],list[str]]:
    existing={item['evidence_id'] for item in current};new=[]
    selections=[];conflicts=[]
    for request in requests:
        selection=select_pages(
            db,run,request['query'],selector_version=selector_version,
            scope_question=scope_question)
        selections.append(selection);conflicts.extend(selection.source_conflicts)
        for row in selection.evidence_rows:
            if row['evidence_id'] not in existing:
                existing.add(row['evidence_id']);new.append(row)
    # Requested evidence receives priority, while the initial context fills the
    # remaining bounded space. No semantic summary replaces source text.
    return _fit_rows(new+current,None if selector_version in COMPLETE_SELECTOR_VERSIONS
                     else MAX_CONTEXT_BYTES),len(new),selections,conflicts


def _visual_inputs(db:Database,uploads,run:dict,selections:list[PageSelection],
                   cache:dict[tuple[str,int],tuple[dict,dict]])->tuple[list[dict],list[dict],list[str]]:
    if uploads is None:return [],[],['UPLOAD_STORE_UNAVAILABLE']
    regions=[];images=[];failures=[];used=0;seen=set()
    for selection in selections:
        for page in selection.selected_pages:
            key=(page.document_id,page.page_number)
            if page.page_number is None or key in seen:continue
            seen.add(key)
            if len(images)>=MAX_VISUAL_PAGES:return regions,images,failures
            cached=cache.get(key)
            if cached is None:
                try:
                    document=db.one('''SELECT id,name,object_key FROM documents
                                       WHERE id=? AND project_id=?''',
                                    (page.document_id,run['project_id']))
                    png,width,height,coordinate_system=render_visual_png(
                        uploads.object_path(document),document['name'],page.page_number)
                    if not png or len(png)>32*1024*1024:raise ValueError('page image size')
                    image_hash=hashlib.sha256(png).hexdigest()
                    bbox=[0.0,0.0,float(width),float(height)]
                    region_id='VR-'+hashlib.sha256(repr((
                        run['id'],page.document_id,page.page_number,bbox,image_hash)).encode()).hexdigest()[:24]
                    region={
                        'region_id':region_id,'document_id':page.document_id,
                        'file_name':page.file_name,'page_number':page.page_number,
                        'bbox':bbox,'coordinate_system':coordinate_system,
                        'source_evidence_ids':list(page.source_evidence_ids),
                        'image_sha256':image_hash,
                    }
                    image={'region_id':region_id,'role':'PAGE_OVERVIEW','png':png}
                    cached=(region,image);cache[key]=cached
                except (DomainError,OSError,ValueError):
                    failures.append(f'{page.file_name} page {page.page_number}: RENDER_UNAVAILABLE')
                    continue
            region,image=cached
            if used+len(image['png'])>MAX_VISUAL_BYTES:
                failures.append(f'{page.file_name} page {page.page_number}: VISUAL_BYTE_LIMIT')
                continue
            regions.append(region);images.append(image);used+=len(image['png'])
    return regions,images,failures


class ProjectEvidenceLoop:
    def __init__(self,db:Database,gateway=None,uploads=None):
        self.db=db;self.gateway=gateway;self.uploads=uploads

    @staticmethod
    def _check_run(run:dict)->None:
        if run['status'] not in ('PARTIAL','COMPLETED'):
            raise DomainError('QA V3 is available only for a completed or partial saved analysis.',409)

    def _prepare_initial(self,run:dict,question:str,selector_version:str)->tuple:
        """Read terminal-snapshot sources once; never keep a DB transaction over HTTP."""
        self._check_run(run)
        selection=select_pages(self.db,run,question,selector_version=selector_version)
        complete=selector_version in COMPLETE_SELECTOR_VERSIONS
        rows=_fit_rows(list(selection.evidence_rows),None if complete else INITIAL_CONTEXT_BYTES)
        if selector_version==LAYOUT_BOUND_SELECTOR_VERSION and any('layout_lines' in row for row in rows):
            raise DomainError('Selector v9 source rows must not carry legacy layout lines.',409)
        source_conflicts=list(selection.source_conflicts)
        from app.gateway import _evidence_source_groups
        if selector_version==LAYOUT_BOUND_SELECTOR_VERSION:
            from app.reference_layout_input import initial_manifest_sha256
            manifest=initial_manifest_sha256(
                selection.selection_id,rows,_evidence_source_groups(rows),source_conflicts)
        else:
            manifest=hashlib.sha256(dumps([
                selection.selection_id,[(item['evidence_id'],item['document_id'],item.get('locator'),
                hashlib.sha256(item['raw_text'].encode('utf-8')).hexdigest(),
                hashlib.sha256(item['layout_lines'].encode('utf-8')).hexdigest() if item.get('layout_lines') else None)
                for item in rows],_evidence_source_groups(rows),source_conflicts]).encode('utf-8')).hexdigest()
        return selection,rows,source_conflicts,manifest

    def preview(self,run:dict,question:str,selector_version:str=SELECTOR_VERSION,route:dict|None=None)->dict:
        if selector_version=='literal-page-selector-10':
            from app.reference_projection_loop import ProjectProjectionLoop
            return ProjectProjectionLoop(self.db,self.gateway,self.uploads).preview(run,question,route)
        extra={}
        if selector_version==LAYOUT_BOUND_SELECTOR_VERSION:
            from app.reference_layout_input import preview_proof
            selection,_,_,manifest=self._prepare_initial(run,question,selector_version)
            extra={'preview_proof':preview_proof(run,question,route,selection.selection_id,manifest),
                   'execution_profile':route}
        else:
            self._check_run(run);selection=select_pages(
                self.db,run,question,selector_version=selector_version)
        return {
            'qa_version':'3','feature':'BOUNDED_EVIDENCE_LOOP','status':'LOOP_READY',
            'model_called':False,'max_model_decisions':MAX_MODEL_DECISIONS,
            'max_evidence_rounds':MAX_MODEL_DECISIONS-1,
            'allowed_tools':['FIND_IDENTIFIER','SEARCH_TEXT'],
            'page_selection':selection.public(),**extra,
        }

    def ask(self,run:dict,question:str,selector_version:str=SELECTOR_VERSION,route:dict|None=None,
            *,preview_proof:dict|None=None)->dict:
        if selector_version=='literal-page-selector-10':
            from app.reference_projection_loop import ProjectProjectionLoop
            return ProjectProjectionLoop(self.db,self.gateway,self.uploads).ask(
                run,question,route,preview_proof=preview_proof)
        complete=selector_version in COMPLETE_SELECTOR_VERSIONS
        layout_bound=selector_version==LAYOUT_BOUND_SELECTOR_VERSION
        if layout_bound and route is None:
            raise DomainError('Selector v9 requires a canonical named text profile.',409)
        selection,rows,source_conflicts,initial_evidence_manifest_sha256=self._prepare_initial(
            run,question,selector_version)
        if preview_proof is not None:
            from app.reference_layout_input import preview_proof as current_proof
            if (not layout_bound or preview_proof!=current_proof(
                    run,question,route,selection.selection_id,initial_evidence_manifest_sha256)):
                raise DomainError('PREVIEW_STALE: Preview this question and profile again before asking.',409)
        selection_trace=[selection.public()]
        common={'qa_version':'3','feature':'BOUNDED_EVIDENCE_LOOP','run_id':run['id'],
                'question':question,
                'source_scope':{'policy':'RUN_SNAPSHOT_ALL_MATCHING_NO_PRECEDENCE',
                                'snapshot_id':run['snapshot_id'],
                                'conflicts':source_conflicts}}
        execution_profile=(
            {'provider':self.gateway.s.provider,
             'text_model':('mock-no-network' if self.gateway.s.provider=='mock'
                           else getattr(self.gateway.s,'cheap_model','unconfigured-text-model')),
             'vision_enabled':bool(self.gateway.s.provider!='mock' and getattr(self.gateway.s,'vision_enabled',False)),
             'vision_model':(getattr(self.gateway.s,'vision_model','unconfigured-vision-model')
                             if self.gateway.s.provider!='mock' and getattr(self.gateway.s,'vision_enabled',False) else None)}
            if complete and self.gateway is not None else None)
        if route is not None:
            execution_profile={'provider':route['provider'],'text_model':route['text_model'],
                               'vision_enabled':False,'vision_model':None,
                               'profile_version':route['profile_version'],'profile_id':route['profile_id'],
                               'max_output_tokens':route['max_output_tokens']}
        if self.gateway is None or self.gateway.s.provider=='mock':
            return {**common,'status':'MODEL_DISABLED','answer':'',
                    'claims':[],'missing':[],'calculations':[],
                    'answer_basis':'QA_V3_INITIAL_EVIDENCE','model_called':False,
                    'model_call_count':0,'retrieved_count':len(rows),
                    'decision_trace':[],'execution_receipts':[],
                    'page_selections':selection_trace,
                    **({'execution_profile':execution_profile} if execution_profile is not None else {})}
        history=[];trace=[];receipts=[];model_calls=0
        visual_priority=[selection];visual_cache={}
        # `visual_regions` is deliberately the final round only: answer
        # citations must never gain authority from an earlier request.  Keep a
        # separate, de-duplicated audit set so every receipt can still be
        # reauthenticated after a multi-round v8 run is persisted.
        visual_regions=[];sent_visual_regions={};visual_failures=[]
        for round_index in range(MAX_MODEL_DECISIONS):
            images=[]
            if route is None and getattr(self.gateway.s,'vision_enabled',False):
                visual_regions,images,visual_failures=_visual_inputs(
                    self.db,self.uploads,run,visual_priority,visual_cache)
                for region in visual_regions:
                    region_id=region['region_id'];previous=sent_visual_regions.get(region_id)
                    if previous is not None and previous.get('image_sha256')!=region.get('image_sha256'):
                        raise DomainError('Reference visual input identity changed between rounds.',409)
                    sent_visual_regions[region_id]=region
            decision_options={
                **({'selector_version':selector_version} if complete else {}),
                'route':route,
                'initial_evidence_manifest_sha256':(
                    initial_evidence_manifest_sha256 if route else None),
            }
            if preview_proof is not None:
                decision_options['expected_prompt_contract_hash']=preview_proof['prompt_contract_hash']
            try:
                result=self.gateway.evidence_decision_v3(
                    run,question,rows,source_conflicts,history,round_index,visual_regions,images,
                    **decision_options)
            except InvalidModelOutput as exc:
                if layout_bound and isinstance(exc.execution_receipt,dict):
                    chain=receipts+[exc.execution_receipt]
                    exc.failure_execution={
                        'failure_execution_version':'reference-failure-execution-1',
                        'receipt_scope':'COMPLETE_CHAIN','execution_receipts':chain,
                        'supplement_round_count':len(chain)-1,
                        'accepted_supplement_request_count':sum(
                            len(item.get('requests',[])) for item in trace
                            if item.get('status')=='NEED_EVIDENCE'),
                    }
                raise
            model_calls+=int(not result.cached);decision=result.data
            if result.execution_receipt is not None:
                receipts.append(result.execution_receipt)
            trace.append({'round':round_index+1,'status':decision['status'],
                          'reason_code':decision['reason_code'],
                          'missing_facts':decision['missing_facts'],
                          'requests':decision['requests'],'cached':result.cached,
                          'visual_region_ids':[item['region_id'] for item in visual_regions]})
            if decision['status']=='ANSWER':
                answer=_enrich(decision['answer'],rows,visual_regions)
                return {**common,**answer,
                        'answer_basis':('MODEL_QA_V3_MULTIMODAL_EVIDENCE_LOOP' if images
                                        else 'MODEL_QA_V3_EVIDENCE_LOOP'),
                        'model_called':bool(model_calls),'model_call_count':model_calls,
                        'retrieved_count':len(rows),'decision_trace':trace,
                        'execution_receipts':receipts,
                        'page_selections':selection_trace,'visual_regions':visual_regions,
                        **({'sent_visual_regions':list(sent_visual_regions.values())} if complete else {}),
                        'visual_failures':visual_failures,
                        **({'execution_profile':execution_profile} if complete else {})}
            if decision['status']!='NEED_EVIDENCE':
                return {**common,'status':decision['status'],'answer':'',
                        'claims':[],'missing':decision['missing_facts'],'calculations':[],
                        'answer_basis':'MODEL_QA_V3_EVIDENCE_LOOP',
                        'model_called':bool(model_calls),'model_call_count':model_calls,
                        'retrieved_count':len(rows),'decision_trace':trace,
                        'execution_receipts':receipts,
                        'page_selections':selection_trace,'visual_regions':visual_regions,
                        **({'sent_visual_regions':list(sent_visual_regions.values())} if complete else {}),
                        'visual_failures':visual_failures,
                        **({'execution_profile':execution_profile} if complete else {})}
            if round_index==MAX_MODEL_DECISIONS-1:
                return {**common,'status':'CANNOT_ANSWER','answer':'',
                        'claims':[],'missing':decision['missing_facts'],'calculations':[],
                        'answer_basis':'QA_V3_ROUND_LIMIT',
                        'model_called':bool(model_calls),'model_call_count':model_calls,
                        'retrieved_count':len(rows),'decision_trace':trace,
                        'execution_receipts':receipts,
                        'page_selections':selection_trace,'visual_regions':visual_regions,
                        **({'sent_visual_regions':list(sent_visual_regions.values())} if complete else {}),
                        'visual_failures':visual_failures,
                        **({'execution_profile':execution_profile} if complete else {})}
            history.extend(decision['requests'])
            rows,new_count,new_selections,new_conflicts=_supplement(
                self.db,run,decision['requests'],rows,selector_version,question)
            selection_trace.extend(item.public() for item in new_selections)
            visual_priority=new_selections+visual_priority
            for conflict in new_conflicts:
                if conflict not in source_conflicts:source_conflicts.append(conflict)
            if new_count==0:
                trace.append({'round':round_index+1,'status':'CANNOT_ANSWER',
                              'reason_code':'NO_NEW_EVIDENCE',
                              'missing_facts':decision['missing_facts'],'requests':[],'cached':True})
                return {**common,'status':'CANNOT_ANSWER','answer':'',
                        'claims':[],'missing':decision['missing_facts'],'calculations':[],
                        'answer_basis':'QA_V3_NO_NEW_EVIDENCE',
                        'model_called':bool(model_calls),'model_call_count':model_calls,
                        'retrieved_count':len(rows),'decision_trace':trace,
                        'execution_receipts':receipts,
                        'page_selections':selection_trace,'visual_regions':visual_regions,
                        **({'sent_visual_regions':list(sent_visual_regions.values())} if complete else {}),
                        'visual_failures':visual_failures,
                        **({'execution_profile':execution_profile} if complete else {})}
        raise AssertionError('bounded QA V3 loop did not terminate')
