"""Deterministic RFI, Submittal and email relationships; no model calls."""
from __future__ import annotations
import hashlib
import heapq
import json
import re
from collections import defaultdict
from app.db import DomainError


_SPACED_SUBMITTAL_ID=re.compile(
    r'^\d{1,2}\s+\d{2}\s+\d{2}(?:\s*[-./]\s*[A-Z0-9][A-Z0-9._/-]{0,20})?')
_COMPACT_ID=re.compile(r'^(?=[A-Z0-9._/-]*\d)[A-Z0-9]+(?:[._/-][A-Z0-9]+)*')
WORKFLOW_SUMMARY_KEYS=('document_type','workflow_contexts','workflow_references','email_content','email_thread',
                       'workflow_type','document_identifier','workflow_role','workflow_status')
WORKFLOW_SUMMARY_SQL_PATHS=','.join(repr(f'$.{key}') for key in WORKFLOW_SUMMARY_KEYS)
RFI_OVERRIDE_ROLES=('UNKNOWN','QUESTION','RESPONSE')
RFI_OVERRIDE_STATUSES=('OPEN','OPEN FOR MANAGER','OPEN FOR REVIEW','OPEN FOR COORDINATOR',
                       'OPEN IN REVIEW','OPEN ANSWERED','OPEN WAITING FOR SUBMISSION',
                       'WAITING FOR SUBMISSION','DRAFT','SUBMITTED','ANSWERED','REJECTED',
                       'CLOSED','CLOSED-DRAFT','CLOSED-REVISED','VOID')
SUBMITTAL_OVERRIDE_STATUSES=('APPROVED','APPROVED AS NOTED','REVISE AND RESUBMIT',
                             'REJECTED','REVIEWED','PENDING')


def projected_workflow_summary(value: object) -> dict:
    """Decode the bounded multi-path SQLite projection used by the workflow endpoint."""
    values=json.loads(value) if isinstance(value,str) else value
    if not isinstance(values,list) or len(values)!=len(WORKFLOW_SUMMARY_KEYS):
        raise ValueError('invalid workflow summary projection')
    summary=dict(zip(WORKFLOW_SUMMARY_KEYS,values))
    summary['document_type']=summary.get('document_type') or 'UNKNOWN'
    return summary


def normalize_identifier(workflow: str, value: object) -> str | None:
    """Return a conservative exact workflow identifier; ordinary words never qualify."""
    if workflow not in {'RFI','SUBMITTAL'} or not isinstance(value,str):return None
    normalized=' '.join(value.upper().translate(str.maketrans({'–':'-','—':'-'})).strip().split())
    normalized=re.sub(rf'^{workflow}\b','',normalized).strip()
    normalized=re.sub(r'^(?:NO\.?|NUMBER)\b','',normalized).lstrip(' #:-')
    if not re.search(r'\d',normalized):return None
    match=_SPACED_SUBMITTAL_ID.match(normalized) if workflow=='SUBMITTAL' else None
    if match:
        identifier=re.sub(r'\s*([-./])\s*',r'\1',' '.join(match.group(0).split()))
    else:
        match=_COMPACT_ID.match(normalized)
        if not match:return None
        identifier=match.group(0)
    if not re.search(r'\d',identifier):return None
    if re.match(r'[A-Z0-9._%+-]*@',normalized[match.end():]):return None
    identifier=identifier.rstrip('._/-')
    if not identifier:return None
    if workflow=='RFI' and identifier.isdigit():identifier=str(int(identifier))
    return identifier


def normalize_workflow_classification(workflow_type: str,identifier: object,role: object,
                                      status: object) -> dict:
    """Validate one explicit human correction without inferring missing business meaning."""
    workflow=str(workflow_type or '').upper()
    if workflow not in {'DETECTED','OTHER','RFI','SUBMITTAL'}:
        raise DomainError('Workflow classification type is not supported',422)
    if workflow in {'DETECTED','OTHER'}:
        if any(value not in (None,'') for value in (identifier,role,status)):
            raise DomainError('Detected or Other classification cannot contain workflow fields',422)
        return {'workflow_type':workflow,'identifier':None,'role':None,'status':None}
    normalized_identifier=normalize_identifier(workflow,identifier)
    if not normalized_identifier:
        raise DomainError('Workflow identifier must be exact and contain a digit',422)
    normalized_role=str(role or '').upper()
    normalized_status=' '.join(str(status).upper().split()) if status not in (None,'') else None
    if workflow=='RFI':
        if normalized_role not in RFI_OVERRIDE_ROLES:
            raise DomainError('RFI role must be Unknown, Question or Response',422)
        if normalized_status not in (None,*RFI_OVERRIDE_STATUSES):
            raise DomainError('RFI status is not in the bounded reviewer list',422)
    else:
        if normalized_role!='SUBMITTAL':
            raise DomainError('Submittal role must remain Submittal',422)
        if normalized_status not in (None,*SUBMITTAL_OVERRIDE_STATUSES):
            raise DomainError('Submittal status is not in the bounded reviewer list',422)
    return {'workflow_type':workflow,'identifier':normalized_identifier,
            'role':normalized_role,'status':normalized_status}


def apply_workflow_classification(summary: dict,override: dict|None) -> dict:
    """Overlay reviewer workflow metadata in memory; never mutate stored parser output."""
    effective={**summary};workflow=(override or {}).get('workflow_type')
    if workflow in (None,'DETECTED'):return effective
    if workflow=='OTHER':
        effective.update({'document_type':'OTHER','workflow_contexts':[],
                          'workflow_type':None,'document_identifier':None,
                          'workflow_role':None,'workflow_status':None})
        return effective
    context={key:override.get(key) for key in ('workflow_type','identifier','role','status')}
    document_type=('SUBMITTAL' if workflow=='SUBMITTAL' else
                   'RFI_RESPONSE' if context['role']=='RESPONSE' else
                   'RFI_QUESTION' if context['role']=='QUESTION' else 'OTHER')
    effective.update({'document_type':document_type,'workflow_contexts':[context],
                      'workflow_type':workflow,'document_identifier':context['identifier'],
                      'workflow_role':context['role'],'workflow_status':context['status']})
    return effective


def workflow_classification_view(summary: dict,override: dict|None) -> dict:
    effective=apply_workflow_classification(summary,override)
    public_override={key:(override or {}).get(key) for key in
                     ('workflow_type','identifier','role','status','version','note','updated_at')}
    if not override:
        public_override={'workflow_type':'DETECTED','identifier':None,'role':None,'status':None,
                         'version':0,'note':'','updated_at':None}
    return {
        'detected':{'document_type':summary.get('document_type','UNKNOWN'),
                    'contexts':_contexts(summary),'references':_references(summary)},
        'effective':{'document_type':effective.get('document_type','UNKNOWN'),
                     'contexts':_contexts(effective),'references':_references(effective)},
        'override':public_override,
        'options':{'rfi_roles':list(RFI_OVERRIDE_ROLES),
                   'rfi_statuses':list(RFI_OVERRIDE_STATUSES),
                   'submittal_statuses':list(SUBMITTAL_OVERRIDE_STATUSES)},
    }


def _key(prefix: str, *values: str) -> str:
    raw='|'.join(values).encode('utf-8')
    return prefix+'-'+hashlib.sha256(raw).hexdigest()[:20]


def _identifier(workflow: str, value: object) -> str | None:
    return normalize_identifier(workflow,value)


def _contexts(summary: dict) -> list[dict]:
    contexts=summary.get('workflow_contexts')
    if not isinstance(contexts,list):contexts=[]
    if not contexts and summary.get('workflow_type'):
        contexts=[{'workflow_type':summary.get('workflow_type'),
                   'identifier':summary.get('document_identifier'),
                   'role':summary.get('workflow_role'),'status':summary.get('workflow_status')}]
    out=[];seen=set()
    for raw in contexts:
        if not isinstance(raw,dict) or raw.get('workflow_type') not in {'RFI','SUBMITTAL'}:continue
        workflow=raw['workflow_type'];identifier=_identifier(workflow,raw.get('identifier'))
        if not identifier:continue
        statuses=[raw.get('status')]
        if isinstance(statuses[0],str):
            statuses=[value.strip() for value in statuses[0].split(' / ') if value.strip()] or [None]
        for status in statuses:
            value={'workflow_type':workflow,'identifier':identifier,
                   'role':raw.get('role'),'status':status}
            identity=(workflow,identifier,value['role'],value['status'])
            if identity not in seen:seen.add(identity);out.append(value)
    return out


def _references(summary: dict) -> list[dict]:
    out=[];seen=set()
    for raw in summary.get('workflow_references') or []:
        if not isinstance(raw,dict) or raw.get('workflow_type') not in {'RFI','SUBMITTAL'}:continue
        workflow=raw['workflow_type'];identifier=_identifier(workflow,raw.get('identifier'))
        identity=(workflow,identifier)
        if identifier and identity not in seen:
            seen.add(identity);out.append({'workflow_type':workflow,'identifier':identifier})
    return out


def _workflow_state(workflow: str, members: list[dict], source_status_conflict: bool = False,
                    ) -> tuple[str,list[str]]:
    primary=[member for member in members if member['source']=='PRIMARY']
    if not primary:
        return 'OPEN',[f'Only references to this {workflow} identifier were found; upload or locate the primary document.']
    if workflow=='RFI':
        questions=[member for member in primary if member.get('role') in {'QUESTION','MIXED'}]
        responses=[member for member in primary if member.get('role') in {'RESPONSE','MIXED'}]
        unknown=[member for member in primary if member.get('role') not in {'QUESTION','RESPONSE','MIXED'}]
        warnings=[];statuses={str(member['status']).upper() for member in primary if member.get('status')}
        if source_status_conflict:
            warnings.append('One RFI source contains multiple explicit statuses; compare the original source sections.')
        elif len(statuses)>1:
            warnings.append('RFI sources with this identifier have different explicit statuses.')
        if len(questions)>1 or len(responses)>1:
            warnings.append('Multiple question or response sources share this RFI identifier; compare the originals.')
        if warnings:return 'AMBIGUOUS',warnings
        if questions and responses:
            return 'LINKED',(['Other sources with this identifier have no explicit Question/Response role.'] if unknown else [])
        if questions:return 'OPEN',['No explicit response document with this identifier was found in the run.']
        if responses:return 'OPEN',['No explicit question document with this identifier was found in the run.']
        return 'OPEN',['This RFI identifier has no explicit Question or Response role; review the source.']
    else:
        if source_status_conflict:
            return 'AMBIGUOUS',['One Submittal source contains multiple explicit statuses; compare the original source sections.']
        statuses={str(member['status']).upper() for member in primary if member.get('status')}
        if len(statuses)>1:return 'AMBIGUOUS',['Submittal sources with this identifier have different explicit statuses.']
        return ('SINGLE' if len(members)==1 else 'LINKED'),[]


def _email_thread_order(values: list[dict],by_key: dict) -> tuple[list[dict],bool]:
    """Put exact local ancestors before replies; retain every member when headers form a cycle."""
    by_id={value['document_id']:value for value in values};children=defaultdict(set)
    indegree={did:0 for did in by_id}
    for child in values:
        thread=child['email_thread']
        for key in [thread.get('parent_message_key'),*(thread.get('reference_keys') or [])]:
            for parent_id in by_key.get(key,[]):
                child_id=child['document_id']
                if parent_id==child_id or parent_id not in by_id or child_id in children[parent_id]:continue
                children[parent_id].add(child_id);indegree[child_id]+=1
    order_key=lambda did:(by_id[did]['file_name'].casefold(),did)
    ready=[order_key(did) for did,count in indegree.items() if count==0];heapq.heapify(ready)
    ordered=[]
    while ready:
        _,did=heapq.heappop(ready);ordered.append(did)
        for child_id in sorted(children[did],key=order_key):
            indegree[child_id]-=1
            if indegree[child_id]==0:heapq.heappush(ready,order_key(child_id))
    cycle=len(ordered)!=len(values)
    if cycle:
        seen=set(ordered);ordered.extend(sorted((did for did in by_id if did not in seen),key=order_key))
    return [by_id[did] for did in ordered],cycle


def _email_threads(documents: list[dict]) -> tuple[list[dict],set[str]]:
    email=[document for document in documents if document.get('email_thread')]
    if not email:return [],set()
    parent={document['document_id']:document['document_id'] for document in email}
    def find(value):
        while parent[value]!=value:
            parent[value]=parent[parent[value]];value=parent[value]
        return value
    def union(left,right):
        a,b=find(left),find(right)
        if a!=b:parent[max(a,b)]=min(a,b)
    by_key=defaultdict(list)
    for document in email:
        key=document['email_thread'].get('message_key')
        if key:by_key[key].append(document['document_id'])
    for ids in by_key.values():
        for other in ids[1:]:union(ids[0],other)
    for document in email:
        thread=document['email_thread']
        for key in [thread.get('parent_message_key'),*(thread.get('reference_keys') or [])]:
            for target in by_key.get(key,[]):union(document['document_id'],target)
    components=defaultdict(list)
    by_id={document['document_id']:document for document in email}
    for did in by_id:components[find(did)].append(by_id[did])
    items=[];linked=set()
    for values in components.values():
        values=sorted(values,key=lambda item:(item['file_name'].casefold(),item['document_id']))
        values,cycle=_email_thread_order(values,by_key)
        keys=[item['email_thread'].get('message_key') for item in values if item['email_thread'].get('message_key')]
        duplicate=len(keys)!=len(set(keys));known=set(keys);external=set();self_reference=False
        message_id_conflict=any(bool(item['email_thread'].get('message_id_conflict')) for item in values)
        for item in values:
            thread=item['email_thread']
            for key in [thread.get('parent_message_key'),*(thread.get('reference_keys') or [])]:
                self_reference=self_reference or bool(key and key==thread.get('message_key'))
                if key and key not in known:external.add(key)
        state=('AMBIGUOUS' if duplicate or message_id_conflict or self_reference or cycle else
               'LINKED' if len(values)>1 else 'SINGLE')
        warnings=[]
        if duplicate:warnings.append('Duplicate Message-ID values require review.')
        if message_id_conflict:warnings.append(
            'One message contains multiple distinct Message-ID values; review the malformed headers.')
        if self_reference:warnings.append('A message references its own Message-ID; review the malformed thread headers.')
        if cycle:warnings.append('Email parent/reference headers form a cycle; compare the original messages.')
        item={'group_id':_key('MAIL',*sorted(keys or [value['document_id'] for value in values])),
              'kind':'EMAIL_THREAD','identifier':None,'state':state,
              'members':[{'document_id':value['document_id'],'file_name':value['file_name'],
                          'document_type':value['document_type'],'role':'MESSAGE','status':None,
                          'source':'PRIMARY'} for value in values],
              'warnings':warnings,
              'external_reference_count':len(external)}
        items.append(item);linked.update(value['document_id'] for value in values)
    return items,linked


def _email_attachments(documents: list[dict],links: list[dict]) -> tuple[list[dict],set[str]]:
    by_id={document['document_id']:document for document in documents};grouped={}
    for raw in links:
        if not isinstance(raw,dict) or raw.get('source_kind')!='EMAIL_ATTACHMENT':continue
        parent_id=raw.get('source_document_id');child_id=raw.get('document_id');index=raw.get('attachment_index')
        if (parent_id not in by_id or child_id not in by_id or parent_id==child_id
                or type(index) is not int or index<0):continue
        if by_id[parent_id]['document_type']!='EMAIL':continue
        identity=(parent_id,child_id,index)
        if identity in grouped:
            grouped[identity]['import_count']+=1
            continue
        parent=by_id[parent_id];child=by_id[child_id]
        grouped[identity]={'group_id':_key('ATTACHMENT',parent_id,child_id,str(index)),
            'kind':'EMAIL_ATTACHMENT','identifier':None,'state':'LINKED',
            'members':[
                {'document_id':parent_id,'file_name':parent['file_name'],
                 'document_type':parent['document_type'],'role':'MESSAGE','status':None,
                 'source':'PARENT_EMAIL'},
                {'document_id':child_id,'file_name':child['file_name'],
                 'document_type':child['document_type'],'role':'ATTACHMENT','status':None,
                 'source':'SELECTED_ATTACHMENT'}],
            'warnings':['This is an explicit import relationship only; workflow role, approval and authority are not inherited.'],
            'external_reference_count':0,'attachment_index':index,
            'content_type':raw.get('content_type'),'import_count':1}
    return list(grouped.values()),{value for identity in grouped for value in identity[:2]}


def build_workflow_index(rows: list[dict],attachment_links: list[dict]|None=None) -> dict:
    documents=[]
    for row in rows:
        summary=row.get('summary',{})
        if isinstance(summary,str):summary=json.loads(summary)
        documents.append({'document_id':row['document_id'],'file_name':row['name'],
                          'document_type':summary.get('document_type','UNKNOWN'),
                          'classification_source':row.get('classification_source','DETECTED'),
                          'contexts':_contexts(summary),'references':_references(summary),
                          'attachments':len(summary.get('attachments') or []),
                          'email_content':summary.get('email_content') or {},
                          'email_thread':summary.get('email_thread') if isinstance(summary.get('email_thread'),dict) else None})
    groups=defaultdict(dict)
    for document in documents:
        for context in document['contexts']:
            identity=(context['workflow_type'],context['identifier'])
            member=groups[identity].setdefault(document['document_id'],{
                'document_id':document['document_id'],'file_name':document['file_name'],
                'document_type':document['document_type'],'roles':set(),'statuses':set(),'source':'PRIMARY',
                'classification_source':document['classification_source']})
            if context.get('role'):member['roles'].add(context['role'])
            if context.get('status'):member['statuses'].add(context['status'])
    for document in documents:
        primary={(context['workflow_type'],context['identifier']) for context in document['contexts']}
        for reference in document['references']:
            identity=(reference['workflow_type'],reference['identifier'])
            if identity in primary:continue
            groups[identity].setdefault(document['document_id'],{
                'document_id':document['document_id'],'file_name':document['file_name'],
                'document_type':document['document_type'],'roles':set(),'statuses':set(),'source':'REFERENCE',
                'classification_source':document['classification_source']})
    items=[];workflow_documents=set()
    for (workflow,identifier),raw_members in sorted(groups.items()):
        members=[];source_status_conflict=False
        for member in sorted(raw_members.values(),key=lambda value:(value['file_name'].casefold(),value['document_id'])):
            roles=member.pop('roles');statuses=member.pop('statuses')
            source_status_conflict=source_status_conflict or len(statuses)>1
            if workflow=='RFI':
                has_question=bool(roles & {'QUESTION','MIXED'})
                has_response=bool(roles & {'RESPONSE','MIXED'})
                member['role']='MIXED' if has_question and has_response else 'QUESTION' if has_question else 'RESPONSE' if has_response else next(iter(roles),None)
            else:
                member['role']='MIXED' if len(roles)>1 else next(iter(roles),None)
            member['status']=' / '.join(sorted(statuses)) or None
            members.append(member);workflow_documents.add(member['document_id'])
        state,warnings=_workflow_state(workflow,members,source_status_conflict)
        items.append({'group_id':_key('WF',workflow,identifier),'kind':workflow,'identifier':identifier,
                      'state':state,'members':members,'warnings':warnings,'external_reference_count':0})
    threads,thread_documents=_email_threads(documents);workflow_documents.update(thread_documents);items.extend(threads)
    attachments,attachment_documents=_email_attachments(documents,attachment_links or [])
    workflow_documents.update(attachment_documents);items.extend(attachments)
    for document in documents:
        if document['document_type']=='EMAIL' and document['document_id'] not in workflow_documents:
            items.append({'group_id':_key('EMAIL',document['document_id']),'kind':'EMAIL','identifier':None,
                          'state':'SINGLE','members':[{'document_id':document['document_id'],
                          'file_name':document['file_name'],'document_type':'EMAIL','role':'MESSAGE',
                          'status':None,'source':'PRIMARY',
                          'classification_source':document['classification_source']}],
                          'warnings':[],'external_reference_count':0})
            workflow_documents.add(document['document_id'])
    order={'RFI':0,'SUBMITTAL':1,'EMAIL_THREAD':2,'EMAIL_ATTACHMENT':3,'EMAIL':4}
    items.sort(key=lambda item:(order[item['kind']],item.get('identifier') or '',item['group_id']))
    return {'items':items,'summary':{
        'documents':len(workflow_documents),'groups':len(items),
        'rfi_groups':sum(item['kind']=='RFI' for item in items),
        'submittal_groups':sum(item['kind']=='SUBMITTAL' for item in items),
        'email_threads':sum(item['kind']=='EMAIL_THREAD' for item in items),
        'email_attachment_links':sum(item['kind']=='EMAIL_ATTACHMENT' for item in items),
        'ambiguous':sum(item['state']=='AMBIGUOUS' for item in items),
        'quoted_email_documents':sum(bool(document['email_content'].get('quoted_history_chars')) for document in documents)}}
