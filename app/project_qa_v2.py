"""Independent QA V2 orchestration and claim-level evidence validation."""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal,InvalidOperation

from app.db import Database,DomainError
from app.answer_diagnostics import NumericEvidenceError
from app.evidence_bundle import EvidenceBundle,build_evidence_bundle
from app.visual_pipeline import render_visual_png
from contracts.runtime_rules import validate_schema

_NUMBER=re.compile(r'(?<![A-Za-z0-9_])[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.\d+)?')
_CALCULATION_REQUEST=re.compile(
    r'(?i)\b(?:add|sum|total|subtract|difference|multiply|product|divide|quotient|'
    r'calculate|arithmetic|percent|percentage)\b|'
    r'相加|总和|合计|相减|差值|相乘|乘积|相除|商')
_VISUAL_QUESTION=re.compile(
    r'(?i)\b(?:shown|depicted|where|location|located|plan|elevation|diagram|detail|symbol|'
    r'orientation|layout|left|right|above|below|visual|drawing)\b|图上|图纸|位置|哪里|平面|立面|详图|符号|方向')
_BUILDING_ENTITY=re.compile(
    r'(?i)\b(?:BUILDING|BLDG\.?)\s*(?:NO\.?\s*)?[#:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{0,15})\b')


def _normalized_text(value:str)->str:return ' '.join(value.split())


def _numbers(value:str)->set[str]:
    return {match.group(0).replace(',','').lstrip('+') for match in _NUMBER.finditer(value)}


def _number_values(value:str)->set[Decimal]:
    """Compare explicit numbers by value without weakening source support."""
    return {Decimal(number) for number in _numbers(value)}


def _number_seen(value:str, texts:list[str])->bool:
    """Report a literal numeric token only; this is not semantic support."""
    try:target=Decimal(value.replace(',','').lstrip('+'))
    except (InvalidOperation,AttributeError):return False
    return any(target in _number_values(text) for text in texts if isinstance(text,str))


def _numeric_detail(reason:str, first_index:int, second_index:int,
                    citations:list[dict], cited_texts:list[str],
                    evidence_rows:list[dict], number:str)->dict:
    """Build the only persisted numeric diagnostic shape, without source text."""
    text_count=sum(citation.get('type')=='TEXT' for citation in citations)
    image_count=sum(citation.get('type')=='IMAGE_REGION' for citation in citations)
    supplied=[(item.get('prompt_text') if isinstance(item.get('prompt_text'),str)
               else item.get('raw_text',''))
              for item in evidence_rows if isinstance(item,dict)]
    detail={
        'reason':reason,
        'text_citation_count':text_count,
        'image_citation_count':image_count,
        'number_seen_in_cited_source':_number_seen(number,cited_texts),
        'number_seen_in_supplied_text':_number_seen(number,supplied),
    }
    if reason=='CLAIM_NUMBER_UNSUPPORTED':
        detail.update(claim_index=first_index,numeric_index=second_index)
    else:
        detail.update(calculation_index=first_index,operand_index=second_index)
    return detail


def _explicit_entities(value:str)->set[tuple[str,str]]:
    """Return only compact labelled identities; never infer an entity from prose."""
    entities=set()
    for match in _BUILDING_ENTITY.finditer(value):
        identifier=match.group(1).upper().rstrip('.')
        if len(identifier)==1 or any(char.isdigit() for char in identifier):
            entities.add(('BUILDING',identifier))
    return entities


def _validate_explicit_entity_scope(data:dict,question:str)->None:
    targets=_explicit_entities(question)
    if not targets or data['status']=='INSUFFICIENT':return
    asserted=_explicit_entities('\n'.join(
        [data.get('answer','')]+[claim.get('text','') for claim in data.get('claims',[])]))
    if asserted-targets:
        raise ValueError('QA V2 answer explicit entity conflicts with the question')
    cited=set()
    for claim in data.get('claims',[]):
        for citation in claim.get('citations',[]):
            value=(citation.get('quote') if citation.get('type')=='TEXT'
                   else citation.get('observation'))
            if isinstance(value,str):cited.update(_explicit_entities(value))
    if not asserted and cited and not cited.intersection(targets):
        raise ValueError('QA V2 citations explicitly identify a different question entity')


def _citation(citation:dict,evidence:dict[str,dict],visual_regions:dict[str,dict])->tuple[str,dict]:
    if citation.get('type')=='TEXT':
        source=evidence.get(citation.get('evidence_id'))
        if source is None:raise ValueError('QA V2 citation is outside the evidence bundle')
        quote=citation.get('quote')
        if not isinstance(quote,str) or not quote or quote not in source['raw_text']:
            raise ValueError('QA V2 citation quote is not exact source text')
        return 'TEXT',source
    if citation.get('type')=='IMAGE_REGION':
        region=visual_regions.get(citation.get('region_id'))
        if region is None:raise ValueError('QA V2 image citation is outside the supplied visual regions')
        for field in ('document_id','page_number','bbox'):
            if citation.get(field)!=region[field]:
                raise ValueError('QA V2 image citation does not match its supplied region')
        if citation.get('needs_review') is not True:
            raise ValueError('QA V2 image citation must remain review-required')
        return 'IMAGE_REGION',region
    raise ValueError('QA V2 citation type is not supported')


def _calculation_value(operator:str,operands:list[str])->Decimal:
    try:values=[Decimal(value.replace(',','')) for value in operands]
    except (InvalidOperation,AttributeError) as exc:raise ValueError('QA V2 calculation operand is invalid') from exc
    result=values[0]
    if operator=='ADD':result=sum(values,Decimal('0'))
    elif operator=='SUBTRACT':
        for value in values[1:]:result-=value
    elif operator=='MULTIPLY':
        for value in values[1:]:result*=value
    elif operator=='DIVIDE':
        try:
            for value in values[1:]:result/=value
        except (InvalidOperation,ZeroDivisionError) as exc:
            raise ValueError('QA V2 calculation divides by zero or is not exact') from exc
    elif operator=='PERCENT_OF':
        if len(values)!=2:
            raise ValueError('QA V2 percentage calculation requires base and percentage operands')
        result=values[0]*values[1]/Decimal('100')
    else:raise ValueError('QA V2 calculation operator is invalid')
    return result


def _decimal_text(value:Decimal)->str:
    text=format(value,'f')
    if '.' in text:text=text.rstrip('0').rstrip('.')
    return '0' if text in ('','-0') else text


def _calculation_result(item:dict)->Decimal:
    result=_calculation_value(item['operator'],item['operands'])
    try:stated=Decimal(item['result'].replace(',',''))
    except (InvalidOperation,AttributeError) as exc:raise ValueError('QA V2 calculation result is invalid') from exc
    if result!=stated:raise ValueError('QA V2 calculation result does not match local Decimal arithmetic')
    return result


def validate_answer_v2(data:dict,evidence_rows:list[dict],question:str,
                       visual_regions:list[dict]|None=None,
                       required_missing:list[str]|None=None)->None:
    validate_schema('project-answer-v2',data)
    evidence={item['evidence_id']:item for item in evidence_rows}
    regions={item['region_id']:item for item in visual_regions or []}
    status=data['status'];claims=data['claims'];missing=data['missing'];calculations=data['calculations']
    if status=='ANSWERED' and (not claims or missing):
        raise ValueError('QA V2 ANSWERED status requires claims and no missing items')
    if status=='PARTIAL' and (not claims or not missing):
        raise ValueError('QA V2 PARTIAL status requires claims and missing items')
    if status=='INSUFFICIENT' and (claims or not missing):
        raise ValueError('QA V2 INSUFFICIENT status requires no claims and at least one missing item')
    expected=' '.join(item['text'] for item in claims)
    if claims and _normalized_text(data['answer'])!=_normalized_text(expected):
        raise ValueError('QA V2 answer contains text outside its atomic claims')
    if not claims and status=='INSUFFICIENT' and len(data['answer'])>1000:
        raise ValueError('QA V2 insufficient answer is too long')
    if required_missing and not set(required_missing).issubset(missing):
        raise ValueError('QA V2 answer omitted a required missing-context code')
    _validate_explicit_entity_scope(data,question)

    calculated=set()
    if calculations and not _CALCULATION_REQUEST.search(question):
        raise ValueError('QA V2 calculation was not explicitly requested')
    for calculation_index,item in enumerate(calculations):
        checked=[_citation(citation,evidence,regions) for citation in item['citations']]
        if any(kind!='TEXT' for kind,_ in checked):
            raise ValueError('QA V2 calculations cannot use image observations')
        sources=[source for _,source in checked]
        supported=set().union(*(_number_values(source['raw_text']) for source in sources))
        for operand_index,operand in enumerate(item['operands']):
            try:value=Decimal(operand.replace(',','').lstrip('+'))
            except (InvalidOperation,AttributeError) as exc:
                raise ValueError('QA V2 calculation operand is invalid') from exc
            if value not in supported:
                raise NumericEvidenceError(
                    'QA V2 calculation operand is not in its cited source text',
                    _numeric_detail('OPERAND_UNSUPPORTED',calculation_index,operand_index,
                                    item['citations'],[source['raw_text'] for source in sources],
                                    evidence_rows,operand))
        calculated.add(_calculation_result(item))

    for claim_index,claim in enumerate(claims):
        checked=[_citation(citation,evidence,regions) for citation in claim['citations']]
        has_image=any(kind=='IMAGE_REGION' for kind,_ in checked)
        supported=set().union(*(_number_values(citation['quote'])
                                for citation in claim['citations']
                                if citation['type']=='TEXT'))
        for numeric_index,match in enumerate(_NUMBER.finditer(claim['text'])):
            number=match.group(0).replace(',','').lstrip('+')
            value=Decimal(number);is_calculated=value in calculated
            if value not in supported and not is_calculated and not has_image:
                raise NumericEvidenceError(
                    'QA V2 claim contains a number absent from its citations',
                    _numeric_detail('CLAIM_NUMBER_UNSUPPORTED',claim_index,numeric_index,
                                    claim['citations'],[source['raw_text'] for kind,source in checked
                                                        if kind=='TEXT'],
                                    evidence_rows,number))


def _evidence_rows(bundle:EvidenceBundle)->list[dict]:
    result=[];seen=set()
    for block in bundle.blocks:
        for citation in block.citations:
            if citation.evidence_id in seen:continue
            seen.add(citation.evidence_id)
            result.append({
                'evidence_id':citation.evidence_id,'document_id':citation.document_id,
                'file_name':citation.file_name,'raw_text':citation.quote,
                'prompt_text':citation.quote,
                'locator':{'page_number':citation.page_number,'path':citation.path,
                           'bbox':citation.bbox,'coordinate_system':citation.coordinate_system},
            })
    return result


def _needs_visual(question:str,bundle:EvidenceBundle)->bool:
    return bool(_VISUAL_QUESTION.search(question) or any(
        citation.extraction_method=='OCR'
        for block in bundle.blocks for citation in block.citations))


def _visual_inputs(db:Database,uploads,run:dict,bundle:EvidenceBundle)->tuple[list[dict],list[dict]]:
    target=next((citation for block in bundle.blocks for citation in block.citations
                 if citation.page_number is not None),None)
    if target is None or uploads is None:return [],[]
    document=db.one('''SELECT id,name,object_key FROM documents
                       WHERE id=? AND project_id=?''',(target.document_id,run['project_id']))
    path=uploads.object_path(document)
    overview,width,height,coordinate_system=render_visual_png(
        path,document['name'],target.page_number)
    bbox=[0.0,0.0,float(width),float(height)]
    crop=None
    if target.bbox and target.coordinate_system==coordinate_system:
        x0,y0,x1,y1=(float(value) for value in target.bbox)
        if 0<=x0<x1<=width and 0<=y0<y1<=height:
            padding=max(12.0,min(width,height)*0.03)
            proposed=[max(0.0,x0-padding),max(0.0,y0-padding),
                      min(float(width),x1+padding),min(float(height),y1+padding)]
            if ((proposed[2]-proposed[0])*(proposed[3]-proposed[1])
                    < float(width)*float(height)*0.9):
                crop=proposed;bbox=proposed
    region_id='VR-'+hashlib.sha256(
        repr((run['id'],target.document_id,target.page_number,bbox)).encode()).hexdigest()[:24]
    region={'region_id':region_id,'document_id':target.document_id,'file_name':document['name'],
            'page_number':target.page_number,'bbox':bbox,'coordinate_system':coordinate_system,
            'source_evidence_ids':[target.evidence_id]}
    images=[{'region_id':region_id,'role':'PAGE_OVERVIEW','png':overview}]
    if crop is not None:
        cropped,_,_,_=render_visual_png(path,document['name'],target.page_number,crop)
        images.append({'region_id':region_id,'role':'LOCAL_CROP','png':cropped})
    return [region],images


def _enrich(data:dict,evidence_rows:list[dict],visual_regions:list[dict])->dict:
    by_id={item['evidence_id']:item for item in evidence_rows}
    by_region={item['region_id']:item for item in visual_regions}
    claims=[]
    for claim in data['claims']:
        citations=[]
        for citation in claim['citations']:
            if citation['type']=='TEXT':
                source=by_id[citation['evidence_id']]
                citations.append({**citation,'file_name':source['file_name'],
                                  'document_id':source['document_id'],**source['locator']})
            else:
                region=by_region[citation['region_id']]
                citations.append({**citation,'file_name':region['file_name'],
                                  'coordinate_system':region['coordinate_system'],
                                  **({'image_sha256':region['image_sha256']}
                                     if region.get('image_sha256') else {})})
        claims.append({'text':claim['text'],'citations':citations})
    return {**data,'claims':claims}


class ProjectQAV2:
    def __init__(self,db:Database,gateway=None,uploads=None):
        self.db=db;self.gateway=gateway;self.uploads=uploads

    def preview(self,run:dict,question:str)->dict:
        if run['status'] not in ('PARTIAL','COMPLETED'):
            raise DomainError('QA V2 is available only for a completed or partial saved analysis.',409)
        bundle=build_evidence_bundle(self.db,run,question)
        return {
            'qa_version':'2','status':'RETRIEVAL_READY' if bundle.blocks else 'INSUFFICIENT',
            'answer_basis':'QA_V2_EVIDENCE_BUNDLE','model_called':False,
            'visual_recommended':_needs_visual(question,bundle),
            'evidence_bundle':bundle.public(),
        }

    def ask(self,run:dict,question:str)->dict:
        if run['status'] not in ('PARTIAL','COMPLETED'):
            raise DomainError('QA V2 is available only for a completed or partial saved analysis.',409)
        bundle=build_evidence_bundle(self.db,run,question);rows=_evidence_rows(bundle)
        common={'qa_version':'2','run_id':run['id'],'question':question,
                'effective_sources':bundle.source_set.public(),'retrieved_count':len(rows)}
        if not rows:
            return {**common,'status':'INSUFFICIENT',
                    'answer':'The active project sources do not contain matching source text.',
                    'claims':[],'missing':list(bundle.missing),'calculations':[],
                    'answer_basis':'NO_MATCHING_EVIDENCE','model_called':False,'cached':False}
        if self.gateway is None or self.gateway.s.provider=='mock':
            return {**common,'status':'MODEL_DISABLED',
                    'answer':'QA V2 assembled an evidence bundle but Mock mode does not generate an answer.',
                    'claims':[],'missing':[],'calculations':[],
                    'answer_basis':'QA_V2_EVIDENCE_BUNDLE','model_called':False,'cached':False,
                    'evidence_bundle':bundle.public()}
        visual_regions=[];images=[];required_missing=[]
        if _needs_visual(question,bundle):
            if self.gateway.s.vision_enabled:
                try:visual_regions,images=_visual_inputs(self.db,self.uploads,run,bundle)
                except (OSError,ValueError):visual_regions,images=[],[]
            if not visual_regions:
                required_missing.append('QUESTION_TIME_VISUAL_CONTEXT_UNAVAILABLE')
        result=self.gateway.answer_v2(
            run,question,rows,list(bundle.source_set.conflicts),visual_regions,images,required_missing)
        validate_answer_v2(result.data,rows,question,visual_regions,required_missing)
        return {**common,**_enrich(result.data,rows,visual_regions),
                'answer_basis':('MODEL_QA_V2_MULTIMODAL_EVIDENCE' if visual_regions
                                else 'MODEL_QA_V2_EVIDENCE'),
                'visual_regions':visual_regions,'model_called':not result.cached,'cached':result.cached}
