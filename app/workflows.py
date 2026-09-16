"""Deterministic RFI, Submittal and email relationships; no model calls."""
from __future__ import annotations
import hashlib
import json
import re
from collections import defaultdict


def _key(prefix: str, *values: str) -> str:
    raw='|'.join(values).encode('utf-8')
    return prefix+'-'+hashlib.sha256(raw).hexdigest()[:20]


def _identifier(workflow: str, value: object) -> str | None:
    if not isinstance(value,str):return None
    normalized=' '.join(value.strip().upper().split())
    if not normalized:return None
    if workflow=='RFI' and normalized.isdigit():return str(int(normalized))
    return normalized


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
        value={'workflow_type':workflow,'identifier':identifier,
               'role':raw.get('role'),'status':raw.get('status')}
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


def _workflow_state(workflow: str, members: list[dict]) -> tuple[str,list[str]]:
    primary=[member for member in members if member['source']=='PRIMARY']
    if not primary:
        return 'OPEN',[f'Only references to this {workflow} identifier were found; upload or locate the primary document.']
    if workflow=='RFI':
        questions=[member for member in primary if member.get('role') in {'QUESTION','MIXED'}]
        responses=[member for member in primary if member.get('role') in {'RESPONSE','MIXED'}]
        if len(questions)>1 or len(responses)>1:
            return 'AMBIGUOUS',['Multiple question or response sources share this RFI identifier; compare the originals.']
        if questions and responses:return 'LINKED',[]
        if questions:return 'OPEN',['No explicit response document with this identifier was found in the run.']
        if responses:return 'OPEN',['No explicit question document with this identifier was found in the run.']
    else:
        statuses={str(member['status']).upper() for member in primary if member.get('status')}
        if len(statuses)>1:return 'AMBIGUOUS',['Submittal sources with this identifier have different explicit statuses.']
        if len(primary)>1:return 'LINKED',[]
    return ('SINGLE' if len(members)==1 else 'OPEN'),[]


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
        keys=[item['email_thread'].get('message_key') for item in values if item['email_thread'].get('message_key')]
        duplicate=len(keys)!=len(set(keys));state='AMBIGUOUS' if duplicate else 'LINKED' if len(values)>1 else 'SINGLE'
        known=set(keys);external=set()
        for item in values:
            thread=item['email_thread']
            for key in [thread.get('parent_message_key'),*(thread.get('reference_keys') or [])]:
                if key and key not in known:external.add(key)
        item={'group_id':_key('MAIL',*sorted(keys or [value['document_id'] for value in values])),
              'kind':'EMAIL_THREAD','identifier':None,'state':state,
              'members':[{'document_id':value['document_id'],'file_name':value['file_name'],
                          'document_type':value['document_type'],'role':'MESSAGE','status':None,
                          'source':'PRIMARY'} for value in values],
              'warnings':(['Duplicate Message-ID values require review.'] if duplicate else []),
              'external_reference_count':len(external)}
        items.append(item);linked.update(value['document_id'] for value in values)
    return items,linked


def build_workflow_index(rows: list[dict]) -> dict:
    documents=[]
    for row in rows:
        summary=row.get('summary',{})
        if isinstance(summary,str):summary=json.loads(summary)
        documents.append({'document_id':row['document_id'],'file_name':row['name'],
                          'document_type':summary.get('document_type','UNKNOWN'),
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
                'document_type':document['document_type'],'roles':set(),'statuses':set(),'source':'PRIMARY'})
            if context.get('role'):member['roles'].add(context['role'])
            if context.get('status'):member['statuses'].add(context['status'])
    for document in documents:
        primary={(context['workflow_type'],context['identifier']) for context in document['contexts']}
        for reference in document['references']:
            identity=(reference['workflow_type'],reference['identifier'])
            if identity in primary:continue
            groups[identity].setdefault(document['document_id'],{
                'document_id':document['document_id'],'file_name':document['file_name'],
                'document_type':document['document_type'],'roles':set(),'statuses':set(),'source':'REFERENCE'})
    items=[];workflow_documents=set()
    for (workflow,identifier),raw_members in sorted(groups.items()):
        members=[]
        for member in sorted(raw_members.values(),key=lambda value:(value['file_name'].casefold(),value['document_id'])):
            roles=member.pop('roles');statuses=member.pop('statuses')
            member['role']='MIXED' if len(roles)>1 else next(iter(roles),None)
            member['status']=' / '.join(sorted(statuses)) or None
            members.append(member);workflow_documents.add(member['document_id'])
        state,warnings=_workflow_state(workflow,members)
        items.append({'group_id':_key('WF',workflow,identifier),'kind':workflow,'identifier':identifier,
                      'state':state,'members':members,'warnings':warnings,'external_reference_count':0})
    threads,thread_documents=_email_threads(documents);workflow_documents.update(thread_documents);items.extend(threads)
    for document in documents:
        if document['document_type']=='EMAIL' and document['document_id'] not in workflow_documents:
            items.append({'group_id':_key('EMAIL',document['document_id']),'kind':'EMAIL','identifier':None,
                          'state':'SINGLE','members':[{'document_id':document['document_id'],
                          'file_name':document['file_name'],'document_type':'EMAIL','role':'MESSAGE',
                          'status':None,'source':'PRIMARY'}],'warnings':[],'external_reference_count':0})
            workflow_documents.add(document['document_id'])
    order={'RFI':0,'SUBMITTAL':1,'EMAIL_THREAD':2,'EMAIL':3}
    items.sort(key=lambda item:(order[item['kind']],item.get('identifier') or '',item['group_id']))
    return {'items':items,'summary':{
        'documents':len(workflow_documents),'groups':len(items),
        'rfi_groups':sum(item['kind']=='RFI' for item in items),
        'submittal_groups':sum(item['kind']=='SUBMITTAL' for item in items),
        'email_threads':sum(item['kind']=='EMAIL_THREAD' for item in items),
        'ambiguous':sum(item['state']=='AMBIGUOUS' for item in items),
        'quoted_email_documents':sum(bool(document['email_content'].get('quoted_history_chars')) for document in documents)}}
