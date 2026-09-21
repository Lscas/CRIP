'use strict';
const $ = id => document.getElementById(id);
const I = window.CIRPI18n;
I.init();
document.querySelectorAll('[data-i18n-initial]').forEach(node=>I.bindText(node,node.dataset.i18nInitial));
const state = {project:null,run:null,records:[],recordCounts:{},recordPagination:null,recordsRun:null,recordLoadPromise:null,takeoffs:[],takeoffsRun:null,workflows:[],workflowPagination:null,workflowsRun:null,workflowLoadPromise:null,connectorStatuses:[],connectorItems:[],refreshPromise:null,unresolvedCalls:[],kind:'MATERIAL',kindTouched:false,settings:null,uploading:false};
const labels = {MATERIAL:'kind.MATERIAL',INSPECTION:'kind.INSPECTION'};
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function elT(tag,key,params={},cls){return I.bindText(el(tag,null,cls),key,params);}
function optionT(key,value=''){return I.bindText(new Option('',value),key);}
function toast(text){I.bindMessage($('toast'),text);$('toast').hidden=false;setTimeout(()=>{$('toast').hidden=true;},6500);}
async function api(path,method='GET',body){
 const headers={'X-CIRP-Client':'browser'};
 if(body!==undefined)headers['Content-Type']='application/json';
 const r=await fetch('/api'+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
 if(!r.ok){let err;try{err=await r.json();}catch{err={detail:'Request failed '+r.status};}throw new Error(I.message(typeof err.detail==='string'?err.detail:JSON.stringify(err.detail)));}
 return r.json();
}
function error(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};}
function remainingText(seconds){const minutes=Math.max(1,Math.round(seconds/60));return minutes<60?`${minutes} min`:`${Math.floor(minutes/60)} hr ${minutes%60} min`;}
function renderProgress(run){
 const progress=run.progress||{percent:0},percent=Math.max(0,Math.min(100,Number(progress.percent)||0));
 $('progress-bar').style.width=percent+'%';$('analysis-progress').setAttribute('aria-valuenow',String(percent));
 const active=['QUEUED','RUNNING'].includes(run.status);
 if(active&&progress.estimated_finish_epoch){
  const finish=new Date(progress.estimated_finish_epoch*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  I.bindText($('progress-summary'),'run.progressActive',{percent,finish,remaining:remainingText(progress.remaining_seconds)});
 }else if(active)I.bindText($('progress-summary'),'run.progressEstimating',{percent});
 else if(percent===100)I.bindText($('progress-summary'),'run.progressFinished',{percent});
 else I.bindText($('progress-summary'),'run.progressStopped',{percent});
 $('analysis-progress').setAttribute('aria-valuetext',$('progress-summary').textContent);
}
async function projects(selected){
 const all=await api('/projects');$('project-select').replaceChildren(optionT('project.select'));
 all.forEach(p=>$('project-select').append(new Option(p.name,p.id)));
 if(selected||all.length){$('project-select').value=selected||all[0].id;await selectProject($('project-select').value);}
}
async function selectProject(id){state.project=id;state.run=null;state.records=[];state.recordCounts={};state.recordPagination=null;state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.unresolvedCalls=[];state.kindTouched=false;
 renderConnectorItems();
 if(!id){I.bindText($('project-name'),'project.none');$('reconciliation-panel').hidden=true;$('save-budget').disabled=true;renderRecords();renderTakeoffs();renderWorkflows();return;}
 const p=await api('/projects/'+id);I.bindText($('project-name'),()=>p.name);
 const budget=await api(`/projects/${id}/budget`);$('budget-limit').value=Number(budget.limit_cny);$('save-budget').disabled=false;
 await refresh();
}
async function refresh(){
 if(!state.project)return;
 const manifest=await api(`/projects/${state.project}/manifest`);$('files-body').replaceChildren();
 manifest.uploads.forEach(u=>{const tr=el('tr'),name=el('td',u.name),verification=elT('td',u.state==='DUPLICATE'?'files.duplicate':u.state==='COMPLETE'?'files.hash':'files.chunk',{offset:u.offset,size:u.size});
  if(u.source_kind==='EMAIL_ATTACHMENT'&&u.source_document_name)name.append(elT('small','files.emailAttachmentSource',{source:u.source_document_name},'muted'));
  if(u.document_id&&/\.(?:eml|msg)$/i.test(u.name)&&['COMPLETE','DUPLICATE'].includes(u.state)){
   const attachments=elT('button','emailAttachments.open',{},'link evidence-button');attachments.onclick=error(()=>showEmailAttachments(u.document_id,u.name));verification.append(attachments);
  }
  if(u.document_id&&['COMPLETE','DUPLICATE'].includes(u.state)){
   const classification=elT('button','workflowClassification.open',{},'link evidence-button');classification.onclick=error(()=>showWorkflowClassification(u.document_id,u.name));verification.append(classification);
  }
  tr.append(name,el('td',(u.size/1024/1024).toFixed(2)+' MB'),I.bindStatus(el('td'),u.state),verification);$('files-body').append(tr);
 });
 I.bindText($('file-total'),'files.total',{count:manifest.documents.length});
 const runs=await api(`/projects/${state.project}/analysis-runs`);$('run-select').replaceChildren(optionT('run.select'));
 runs.forEach(r=>$('run-select').append(I.bindText(new Option('',r.id),()=>r.created_at.slice(0,19).replace('T',' ')+' · '+I.status(r.status))));
 if(!state.run&&runs.length)state.run=runs[0].id;
 if(state.run){$('run-select').value=state.run;await refreshRun();}else{renderRecords();renderTakeoffs();renderWorkflows();I.bindText($('run-state'),'run.notStarted');}
 $('start').disabled=state.uploading||!manifest.documents.length||state.unresolvedCalls.length>0;
}
async function refreshRun(forceRecords=false){
 if(!state.run)return;
 if(state.refreshPromise){await state.refreshPromise;if(!forceRecords)return;}
 const task=(async()=>{
  const rid=state.run;const run=await api('/analysis-runs/'+rid);
  if(rid!==state.run)return;
  const active=['QUEUED','RUNNING'].includes(run.status);
  const cost=await api(`/analysis-runs/${rid}/cost`);
 if(forceRecords||(!active&&state.recordsRun!==rid)){
   const overview=await api(`/analysis-runs/${rid}/record-summaries?limit=1`);
   if(rid!==state.run)return;
   state.recordCounts=overview.counts;
   const primaryKind=['MATERIAL','INSPECTION'].find(k=>state.recordCounts[k]) || 'MATERIAL';
   if(!state.kindTouched)state.kind=primaryKind;
   await loadRecordPage(true);
   if(rid!==state.run)return;
   state.recordsRun=rid;
  }
  if(forceRecords||(!active&&state.takeoffsRun!==rid)){
   state.takeoffs=await api(`/analysis-runs/${rid}/takeoffs`);state.takeoffsRun=rid;
  }
  if(forceRecords||(!active&&state.workflowsRun!==rid)){
   await loadWorkflowPage(true);
   if(rid!==state.run)return;
   state.workflowsRun=rid;
  }
  state.unresolvedCalls=await api(`/projects/${state.project}/unresolved-model-calls`);
  I.bindStatus($('run-state'),run.status);I.bindText($('run-message'),()=>I.t('run.message',{stage:I.message(run.stage),message:I.message(run.message)}));
  const c=run.coverage;const done=(c.fragments_extracted||0)+(c.fragments_need_review||0);
  renderProgress(run);
  I.bindText($('coverage'),'run.coverage',{processed:c.files_processed||0,total:c.files_total||0,done,fragments:c.fragments_total||0,review:c.fragments_need_review||0});
   I.bindText($('cost'),'run.cost',{spent:Number(cost.spent_cny).toFixed(4),reserved:Number(cost.reserved_cny).toFixed(4),limit:Number(cost.limit_cny).toFixed(2)});
   if(document.activeElement!==$('budget-limit'))$('budget-limit').value=Number(cost.limit_cny);
   const perf=run.performance||{},stages=Object.entries(perf).filter(([name])=>name.startsWith('stage.'));
   const elapsed=stages.reduce((sum,[,value])=>sum+value.total_ms,0)/1000,responses=perf['model.response']||{samples:0,average_ms:0};
   I.bindText($('performance'),'run.performance',{elapsed:elapsed.toFixed(1),requests:responses.samples,average:(responses.average_ms/1000).toFixed(2)});
   I.bindText($('coverage-detail'),()=>JSON.stringify({coverage:c,performance:perf,cost,capabilities:run.capabilities},null,2));
  $('pause').disabled=!active;
  renderReconciliation(state.unresolvedCalls);
  if(state.unresolvedCalls.length)$('start').disabled=true;
  $('resume').disabled=!['PAUSED','PAUSED_PROVIDER','PAUSED_BUDGET','INTERRUPTED'].includes(run.status)||state.unresolvedCalls.length>0;
  $('export-json').disabled=active;$('export-xlsx').disabled=active;renderRecords();renderTakeoffs();renderWorkflows();
 })();
 state.refreshPromise=task;
 try{await task;}finally{if(state.refreshPromise===task)state.refreshPromise=null;}
}
function diagnosticText(call){
 const d=call.diagnostic||{};const parts=[];
 if(d.kind)parts.push(String(d.kind));if(d.status)parts.push('HTTP '+String(d.status));if(d.class)parts.push(String(d.class));
 if(d.provider_code)parts.push(String(d.provider_code));if(d.retry_after)parts.push('Retry-After '+String(d.retry_after));
 const providerId=call.provider_request_id||d.provider_request_id;if(providerId)parts.push(I.t('reconcile.requestId',{id:providerId}));
 return parts.join(' · ')||I.t('reconcile.legacyUnknown');
}
function renderReconciliation(calls){
 const panel=$('reconciliation-panel'),list=$('unresolved-calls');list.replaceChildren();panel.hidden=!calls.length;
 calls.forEach(call=>{const card=el('article',null,'reconciliation-call');
  const text=el('div');text.append(el('strong',call.model),elT('small','reconcile.call',{id:call.id,run:call.run_id}),el('small',diagnosticText(call)),elT('small','reconcile.reserved',{amount:call.reserved_cny}));
  const button=elT('button','reconcile.open',{},'outline');button.disabled=call.state!=='UNKNOWN';button.onclick=()=>openReconciliation(call);
  card.append(text,button);list.append(card);
 });
}
function openReconciliation(call){
 $('reconcile-form').reset();$('reconcile-call-id').value=call.id;$('reconcile-resolution').value='NOT_BILLED';$('reconcile-amount').value='0';syncReconcileAmount();
 I.bindText($('reconcile-call-summary'),()=>I.t('reconcile.summary',{model:call.model,amount:call.reserved_cny,diagnostic:diagnosticText(call)}));
 $('reconcile-dialog').showModal();
}
function syncReconcileAmount(){
 const billed=$('reconcile-resolution').value==='BILLED';$('reconcile-amount').disabled=!billed;$('reconcile-amount').required=billed;$('reconcile-amount').min=billed?'0.000001':'0';if(!billed)$('reconcile-amount').value='0';
}
function sourceIds(c){const ids=new Set(c.evidence_ids||c.related_evidence_ids||[]);(c.claims||[]).forEach(x=>ids.add(x.evidence_id));return [...ids];}
async function loadRecordPage(reset=false){
 if(!state.run)return;
 if(state.recordLoadPromise)await state.recordLoadPromise;
 const rid=state.run,kind=state.kind,previous=reset?[]:state.records;
 const offset=reset?0:(state.recordPagination?.next_offset);
 if(offset==null&&!reset)return;
 const task=(async()=>{
  const page=await api(`/analysis-runs/${rid}/record-summaries?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=100`);
  if(rid!==state.run||kind!==state.kind)return;
  const seen=new Set(previous.map(item=>item.record.meta.record_id));
  state.records=previous.concat(page.items.filter(item=>!seen.has(item.record.meta.record_id)));
  state.recordCounts=page.counts;state.recordPagination=page.pagination;
 })();
 state.recordLoadPromise=task;
 try{await task;}finally{if(state.recordLoadPromise===task)state.recordLoadPromise=null;}
}
async function loadWorkflowPage(reset=false){
 if(!state.run)return;
 if(state.workflowLoadPromise)await state.workflowLoadPromise;
 const rid=state.run,previous=reset?[]:state.workflows;
 const offset=reset?0:state.workflowPagination?.next_offset;
 if(offset==null&&!reset)return;
 const task=(async()=>{
  const page=await api(`/analysis-runs/${rid}/workflows?offset=${offset}&limit=500`);
  if(rid!==state.run)return;
  const seen=new Set(previous.map(item=>item.group_id));
  state.workflows=previous.concat(page.items.filter(item=>!seen.has(item.group_id)));
  state.workflowPagination=page.pagination;
 })();
 state.workflowLoadPromise=task;
 try{await task;}finally{if(state.workflowLoadPromise===task)state.workflowLoadPromise=null;}
}
function renderRecords(){
 Object.keys(labels).forEach(k=>{$('count-'+k).textContent=state.recordCounts?.[k]||0;});
 document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.kind===state.kind));
 const query=$('filter').value.toLowerCase();const rows=state.records.filter(x=>x.record.kind===state.kind&&JSON.stringify(x.record.candidate).toLowerCase().includes(query));
 $('results-body').replaceChildren();$('empty').hidden=rows.length>0;
  rows.forEach(row=>{const r=row.record,c=r.candidate,d=r.display||{};const tr=el('tr',null,'click-row');
   const name=el('td');const btn=el('button',d.name||c.name||c.requirement||c.subject,'link');btn.onclick=()=>showRecord(row);name.append(btn);
   const details=[];
   if(r.kind==='MATERIAL'){
    if(c.design_properties?.length)details.push(c.design_properties.map(x=>x.name+': '+x.value+(x.unit?' '+x.unit:'')).join(' · '));
    if(d.specification_section)details.push('Specification '+d.specification_section);
    if(c.quantity)details.push('Quantity '+c.quantity.value+' '+c.quantity.unit);
   }else{
    if(d.activity)details.push(d.activity);if(d.specification_section)details.push('Specification '+d.specification_section);if(d.performer)details.push('Performer '+d.performer);
   }
   const detail=details.join(' · ');
  name.append(el('small',detail));
  const status=el('td');status.append(I.bindStatus(el('span',null,'badge'),c.requirement_status||c.resolution_status||c.reason_code));status.append(elT('small','verify.status.'+(row.verification?.status||'NOT_CHECKED'),{},'verification-summary'));
  const review=el('td');review.append(I.bindStatus(el('span',null,'badge '+(r.review.status==='PENDING'?'warn':'')),r.review.status));
   const evidence=el('td');sourceIds(c).forEach((eid,index)=>{const b=elT('button','record.sourceNumber',{number:index+1},'link evidence-button');b.onclick=error(()=>showEvidence(eid));evidence.append(b);});
  tr.append(name,status,review,evidence);$('results-body').append(tr);
 });
 const total=state.recordCounts?.[state.kind]||0,loaded=state.records.length,more=state.recordPagination?.next_offset!=null;
 I.bindText($('results-page-status'),'results.loaded',{loaded,total});$('results-load-more').hidden=!more;$('results-load-more').disabled=!more;
}
function renderTakeoffs(){
 const rows=[];const geometry=[];
 (state.takeoffs||[]).forEach(doc=>{
  (doc.takeoffs||[]).forEach(item=>rows.push({...item,file_name:doc.file_name}));
  (doc.geometry_summaries||[]).forEach(item=>geometry.push({file_name:doc.file_name,...item}));
 });
 I.bindText($('takeoff-total'),'takeoff.total',{count:rows.length});$('takeoff-body').replaceChildren();
 if(!rows.length){const tr=el('tr');const td=elT('td','takeoff.empty',{},'muted');td.colSpan=4;tr.append(td);$('takeoff-body').append(tr);}
 rows.forEach(item=>{const tr=el('tr');
  const source=el('td');source.append(el('strong',item.label),el('small',item.file_name+' · '+item.kind));
  tr.append(source,el('td',item.method),el('td',`${item.value} ${item.unit}`),I.bindStatus(el('td'),item.review_status));
  $('takeoff-body').append(tr);
 });
 if(geometry.length)I.bindText($('geometry-detail'),()=>JSON.stringify(geometry,null,2));
 else I.bindText($('geometry-detail'),'takeoff.geometryEmpty');
}
function workflowCodeLabel(section,value){
 const key=`workflow.${section}.${value}`,label=I.t(key);
 return label===key?String(value??'').toLowerCase().replaceAll('_',' ').replace(/(^|[ /-])\w/g,match=>match.toUpperCase()):label;
}
function renderWorkflows(){
 const rows=state.workflows||[],total=state.workflowPagination?.total??rows.length,more=state.workflowPagination?.next_offset!=null;
 I.bindText($('workflow-total'),total?'workflow.loaded':'workflow.zero',{loaded:rows.length,total});
 $('workflow-load-more').hidden=!more;$('workflow-load-more').disabled=!more;$('workflow-body').replaceChildren();
 if(!rows.length){const tr=el('tr');const td=elT('td','workflow.empty',{},'muted');td.colSpan=4;tr.append(td);$('workflow-body').append(tr);return;}
 rows.forEach(item=>{const tr=el('tr');const title=el('td');title.append(el('strong',workflowCodeLabel('kind',item.kind)+(item.identifier?' '+item.identifier:'')));if(item.kind==='EMAIL_ATTACHMENT'&&item.import_count>1)title.append(elT('small','workflow.importCount',{count:item.import_count}));
  const documents=el('td');(item.members||[]).forEach(member=>{const a=el('a',member.file_name,'link evidence-button');a.href='/api/documents/'+member.document_id+'/file';a.target='_blank';a.rel='noopener';documents.append(a);const details=[member.role&&workflowCodeLabel('role',member.role),member.status&&workflowCodeLabel('status',member.status),member.source&&workflowCodeLabel('source',member.source),member.classification_source&&I.t('workflowClassification.source.'+member.classification_source)].filter(Boolean);if(details.length)documents.append(el('small',details.join(' · ')));});
  const status=el('td',workflowCodeLabel('state',item.state),'badge '+(item.state==='AMBIGUOUS'?'warn':''));status.dataset.code=item.state;
  tr.append(title,status,documents,el('td',(item.warnings||[]).join(' ')));$('workflow-body').append(tr);
 });
}
function selectedConnector(){return $('connector-provider').value;}
async function loadConnectorStatus(){
 state.connectorStatuses=await api('/connectors');const provider=selectedConnector();const current=state.connectorStatuses.find(item=>item.provider===provider);
 I.bindText($('connector-status'),current?.configured?'connector.connected':'connector.disconnected',{provider:current?.label||provider});
 $('connector-root').disabled=!current?.configured;$('connector-disconnect').disabled=!current?.configured;
}
async function listConnectorItems(folderId=''){
 const provider=selectedConnector();const suffix=folderId?'?folder_id='+encodeURIComponent(folderId):'';
 state.connectorItems=await api(`/connectors/${provider}/items${suffix}`);renderConnectorItems();
}
function renderConnectorItems(){
 const body=$('connector-body');body.replaceChildren();
 if(!state.connectorItems.length){const tr=el('tr');const td=elT('td','connector.empty',{},'muted');td.colSpan=4;tr.append(td);body.append(tr);return;}
 state.connectorItems.forEach(item=>{const tr=el('tr');const action=el('td');const button=elT('button',item.kind==='FOLDER'?'connector.browse':'connector.import',{},'outline');
  button.disabled=item.kind==='FILE'&&!state.project;
  button.onclick=error(async()=>{if(item.kind==='FOLDER')await listConnectorItems(item.remote_id);else{button.disabled=true;await api(`/projects/${state.project}/connector-imports`,'POST',{provider:selectedConnector(),remote_id:item.remote_id});toast(I.t('connector.imported',{name:item.name}));await refresh();}});action.append(button);
  const hasSize=item.size!==null&&item.size!==undefined&&Number.isFinite(Number(item.size));
  tr.append(el('td',item.name),I.bindStatus(el('td'),item.kind),el('td',hasSize?(Number(item.size)/1024/1024).toFixed(2)+' MB':'—'),action);body.append(tr);
 });
}
async function showEmailAttachments(documentId,fileName){
 const listing=await api(`/documents/${documentId}/email-attachments`);$('drawer').hidden=false;I.bindText($('drawer-title'),'emailAttachments.title');const body=$('drawer-body');body.replaceChildren();
 body.append(elT('p','emailAttachments.description',{file:fileName},'muted'));
 if(!listing.attachments.length){body.append(elT('p','emailAttachments.empty',{},'muted'));return;}
 const importable=listing.attachments.filter(item=>item.importable);
 if(importable.length>1){
  const actions=el('div',null,'row');const batch=elT('button','emailAttachments.importAll',{count:importable.length},'primary');batch.disabled=!state.project;
  batch.onclick=error(async()=>{batch.disabled=true;const result=await api(`/projects/${state.project}/email-attachment-imports/batch`,'POST',{document_id:documentId,attachments:importable.map(item=>({attachment_index:item.attachment_index,expected_sha256:item.sha256}))});$('drawer').hidden=true;toast(I.t('emailAttachments.importedAll',{count:result.count}));await refresh();});
  actions.append(batch);body.append(actions);
 }
 listing.attachments.forEach(item=>{const card=el('article',null,'verification-field');card.append(el('strong',item.name),el('small',`${item.content_type} · ${item.size==null?'—':(item.size/1024).toFixed(1)+' KB'}`));
  const button=elT('button','emailAttachments.import',{},'outline');button.disabled=!item.importable||!state.project;
  button.onclick=error(async()=>{button.disabled=true;await api(`/projects/${state.project}/email-attachment-imports`,'POST',{document_id:documentId,attachment_index:item.attachment_index,expected_sha256:item.sha256});$('drawer').hidden=true;toast(I.t('emailAttachments.imported',{name:item.name}));await refresh();});
  card.append(button);if(!item.importable)card.append(elT('small','emailAttachments.unsupported'));body.append(card);
 });
}
function classificationText(value){
 const context=value.contexts?.[0];
 return context?[context.workflow_type,context.identifier,context.role,context.status].filter(Boolean).join(' · '):(value.document_type||'UNKNOWN');
}
async function showWorkflowClassification(documentId,fileName){
 if(!state.run){toast(I.t('workflowClassification.noRun'));return;}
 const rid=state.run,data=await api(`/analysis-runs/${rid}/documents/${documentId}/workflow-classification`);
 $('drawer').hidden=false;I.bindText($('drawer-title'),'workflowClassification.title');const body=$('drawer-body');body.replaceChildren();
 body.append(el('h3',fileName),elT('p','workflowClassification.description',{},'muted'),elT('strong','workflowClassification.detected'),el('p',classificationText(data.detected)),elT('strong','workflowClassification.effective'),el('p',classificationText(data.effective)));
 const form=el('form',null,'classification-form');
 const typeSelect=el('select');typeSelect.id='workflow-classification-type';
 ['DETECTED','OTHER','RFI','SUBMITTAL'].forEach(value=>typeSelect.append(optionT('workflowClassification.type.'+value,value)));
 typeSelect.value=data.override.workflow_type;
 const identifier=el('input');identifier.id='workflow-classification-identifier';identifier.maxLength=64;identifier.value=data.override.identifier||'';
 const role=el('select');role.id='workflow-classification-role';
 const status=el('select');status.id='workflow-classification-status';
 const note=el('input');note.id='workflow-classification-note';note.maxLength=1000;note.value=data.override.note||'';
 function addField(key,control){const label=el('label');label.append(elT('span',key),control);form.append(label);}
 addField('workflowClassification.type',typeSelect);addField('workflowClassification.identifier',identifier);addField('workflowClassification.role',role);addField('workflowClassification.status',status);addField('workflowClassification.note',note);
 function sync(){
  const value=typeSelect.value,isRfi=value==='RFI',isSubmittal=value==='SUBMITTAL',active=isRfi||isSubmittal;
  identifier.disabled=!active;role.disabled=!active;status.disabled=!active;role.replaceChildren();status.replaceChildren(optionT('workflowClassification.none'));
  (isRfi?data.options.rfi_roles:isSubmittal?['SUBMITTAL']:[]).forEach(value=>role.append(new Option(workflowCodeLabel('role',value),value)));
  (isRfi?data.options.rfi_statuses:isSubmittal?data.options.submittal_statuses:[]).forEach(value=>status.append(new Option(workflowCodeLabel('status',value),value)));
  if(active){role.value=data.override.role||role.options[0]?.value||'';status.value=data.override.status||'';}else{identifier.value='';}
 }
 typeSelect.onchange=sync;sync();
 const save=elT('button','workflowClassification.save',{},'primary');save.type='submit';form.append(save,elT('p','workflowClassification.viewOnly',{},'muted'));
 form.onsubmit=error(async event=>{event.preventDefault();save.disabled=true;try{await api(`/analysis-runs/${rid}/documents/${documentId}/workflow-classification`,'POST',{expected_version:data.override.version,workflow_type:typeSelect.value,identifier:identifier.disabled?null:identifier.value,role:role.disabled?null:role.value,status:status.disabled||!status.value?null:status.value,note:note.value});$('drawer').hidden=true;await loadWorkflowPage(true);state.workflowsRun=rid;renderWorkflows();toast(I.t('workflowClassification.saved'));}finally{save.disabled=false;}});
 body.append(form);
}
async function showRecord(row){
  const listDisplay=row.record.display;
  if(!row.record.candidate.candidate_key){
   try{row=await api(`/records/${row.record.meta.record_id}`);}catch(e){toast(e.message);return;}
   if(listDisplay)row.record.display=listDisplay;
  }
  const r=row.record,c=r.candidate,d=r.display||{};$('drawer').hidden=false;I.bindText($('drawer-title'),labels[r.kind]);const body=$('drawer-body');body.replaceChildren();
  body.append(el('h3',d.name||c.name||c.requirement||c.subject));
  if(r.kind==='MATERIAL'){
   if(c.design_properties?.length)body.append(el('p',c.design_properties.map(x=>x.name+': '+x.value+(x.unit?' '+x.unit:'')).join(' · ')));
   if(d.specification_section)body.append(el('p','Specification section: '+d.specification_section));
   if(c.quantity)body.append(el('p','Quantity: '+c.quantity.value+' '+c.quantity.unit));
  }else{
   if(d.activity)body.append(el('p','Activity / requirement: '+d.activity));
   if(d.specification_section)body.append(el('p','Specification section: '+d.specification_section));
   if(d.performer)body.append(el('p','Performer: '+d.performer));
  }
  body.append(elT('p','record.note',{},'muted'));body.append(verificationPanel(row));
  const editor=el('details');editor.append(elT('summary','record.structuredEditor'));
  const area=el('textarea');area.value=JSON.stringify(c,null,2);area.setAttribute('data-i18n-aria-label','record.json');I.applyElement(area);editor.append(area);body.append(editor);
 const note=el('input');note.setAttribute('data-i18n-placeholder','record.reviewNote');note.setAttribute('data-i18n-aria-label','record.reviewNoteLabel');I.applyElement(note);body.append(note);
 const actions=el('div',null,'row');
 ['ACCEPTED','EDITED','REJECTED'].forEach((action,i)=>{const b=elT('button',['record.accept','record.edit','record.reject'][i],{},i===0?'primary':'outline');b.onclick=error(async()=>{
  await api(`/records/${r.meta.record_id}/review`,'POST',{action,expected_version:row.review_version,note:note.value,...(action==='EDITED'?{candidate:JSON.parse(area.value)}:{})});
  $('drawer').hidden=true;await refreshRun(true);toast('Review saved');});actions.append(b);});body.append(actions,el('hr'));
  sourceIds(c).forEach((id,index)=>{const b=elT('button','record.sourceNumber',{number:index+1},'link evidence-button');b.onclick=error(()=>showEvidence(id));body.append(b);});
 const history=elT('button','record.history',{},'outline');history.onclick=error(async()=>{const data=await api(`/records/${r.meta.record_id}/history`);body.append(el('pre',JSON.stringify(data,null,2)));});body.append(history);
}
async function showEvidence(id,range=null,runId=state.run){
 const data=await api(`/analysis-runs/${runId}/evidence/${encodeURIComponent(id)}`);const e=data.evidence;
 $('drawer').hidden=false;I.bindText($('drawer-title'),e.extraction_method==='VISION'?'evidence.visionTitle':'evidence.title');const body=$('drawer-body');body.replaceChildren();
 body.append(el('h3',data.file_name),I.bindText(el('p',null,'muted'),()=>I.t('evidence.revision',{date:e.internal_revision_date||I.t('evidence.unknownDate')})));
 body.append(el('pre',JSON.stringify(e.locator,null,2)));
 if(e.extraction_method==='VISION')body.append(elT('p','evidence.visionWarning',{},'notice'));
 const original=el('pre');
 if(range&&Number.isInteger(range.start)&&Number.isInteger(range.end)&&range.start>=0&&range.end<=Array.from(e.raw_text).length){
  const chars=Array.from(e.raw_text);original.append(document.createTextNode(chars.slice(0,range.start).join('')),el('mark',chars.slice(range.start,range.end).join('')),document.createTextNode(chars.slice(range.end).join('')));
  }else original.textContent=e.raw_text;body.append(original);
 if(e.image_crop_uri){const image=el('img',null,'citation-preview');image.alt=I.t('evidence.pageImageAlt');image.src=e.image_crop_uri;body.append(image);}
 const a=elT('a','evidence.open',{},'button outline');a.href='/api/documents/'+e.document_id+'/file';a.target='_blank';a.rel='noopener';body.append(a);
 body.append(elT('p',e.extraction_method==='VISION'?'evidence.visionNote':'evidence.note',{},'muted'));
}
function verificationPanel(row){
 const root=el('section',null,'verification-panel');root.dataset.verificationPanel='true';
 const report=row.verification||{status:'NOT_CHECKED',fields:[]};
 root.append(elT('h3','verify.title'),elT('span','verify.status.'+report.status,{},'badge warn'),elT('p','verify.disclaimer',{},'muted'));
 const toolbar=el('div',null,'row');
 const check=elT('button','verify.check',{},'outline');
 check.onclick=error(async()=>{await api(`/records/${row.record.meta.record_id}/verification`,'POST',{expected_version:row.review_version,semantic:false});await refreshRun(true);const fresh=state.records.find(x=>x.record.meta.record_id===row.record.meta.record_id);if(fresh)showRecord(fresh);});
 const semantic=elT('button','verify.semantic',{},'outline');semantic.disabled=!state.settings?.live_ready;
 semantic.onclick=error(async()=>{const job=await api(`/records/${row.record.meta.record_id}/verification`,'POST',{expected_version:row.review_version,semantic:true});root.append(elT('p','verify.queued',{id:job.id}));semantic.disabled=true;});
 toolbar.append(check,semantic);root.append(toolbar);
 if(report.processing_basis){const detail=el('details');detail.append(elT('summary','verify.processingBasis'),el('pre',JSON.stringify(report.processing_basis,null,2)));root.append(detail);}
 const priority=f=>/^\/(name|requirement|subject|activity)$/.test(f.path)?0:(f.path.startsWith('/design_properties/')?1:2);
 const visibleFields=[...(report.fields||[])].sort((a,b)=>priority(a)-priority(b));
 for(const field of visibleFields){
  const card=el('article',null,'verification-field');
  card.append(el('strong',field.label===field.path?field.path:field.label+' · '+field.path),el('p',field.claim),elT('span','verify.status.'+field.status,{},'badge'),elT('small','verify.method',{method:field.method}));
  for(const issue of field.issues||[])card.append(el('p',issue,'muted'));
  for(const q of field.citations||[]){
   const b=el('button',q.citation_id.slice(0,17)+'… · '+q.file_name,'link evidence-button');
   b.onclick=error(()=>showCitation(row.record.meta.record_id,q.citation_id));
   card.append(b,elT('small','verify.role.'+q.role));
   if(q.text_basis==='MODEL_VISION_OUTPUT')card.append(elT('p','verify.modelVisionContext',{},'muted'));
   else card.append(el('blockquote',q.quote));
  }
  if(!field.citations?.length)card.append(elT('p','verify.noQuote',{},'muted'));
  root.append(card);
 }
 return root;
}
async function showCitation(recordId,citationId){
 const result=await api(`/records/${recordId}/citations/${citationId}`);const q=result.citation;
 await showEvidence(q.evidence_id,{start:q.start,end:q.end},result.run_id);
 const body=$('drawer-body');body.append(elT('p','verify.location',{id:q.citation_id,hash:q.file_sha256}));
 if(q.locator.page_number&&q.file_name.toLowerCase().endsWith('.pdf')){
  const b=elT('button','verify.preview',{},'outline');
  b.onclick=error(async()=>{b.disabled=true;const image=el('img',null,'citation-preview');image.alt=I.t('verify.previewAlt');image.src=`/api/records/${recordId}/citations/${citationId}/preview`;image.onerror=()=>{image.remove();body.append(elT('p','verify.previewFailed',{},'muted'));b.disabled=false;};body.append(image);});body.append(b);
 }
}

async function uploadFiles(files){
 if(!state.project){toast('Create or select a project first');return;}
 if(state.uploading){toast('An upload is already in progress');return;}
 state.uploading=true;$('start').disabled=true;
 try{for(const file of files){
  const key=['cirp-upload',state.project,file.webkitRelativePath||file.name,file.size,file.lastModified].join(':');
  let u;const saved=localStorage.getItem(key);
  if(saved){try{u=await api('/uploads/'+saved);}catch{localStorage.removeItem(key);}}
  if(!u||u.state==='ABORTED'){u=await api(`/projects/${state.project}/uploads`,'POST',{name:file.name,size:file.size});localStorage.setItem(key,u.id);}
  if(!['COMPLETE','DUPLICATE'].includes(u.state)){
   // Verify accepted chunks so a replaced same-name file cannot mix content.
   for(const chunk of u.chunks||[]){
    const digest=await crypto.subtle.digest('SHA-256',await file.slice(chunk.offset,chunk.offset+chunk.size).arrayBuffer());
    const hex=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hex!==chunk.checksum){localStorage.removeItem(key);await api(`/uploads/${u.id}/abort`,'POST');throw new Error('The resumed file contents changed. Select the file again to upload.');}
   }
   for(let offset=u.offset;offset<file.size;){
    const part=file.slice(offset,offset+state.settings.upload_chunk_bytes);
    const r=await fetch(`/api/uploads/${u.id}/chunk?offset=${offset}`,{method:'PUT',headers:{'X-CIRP-Client':'browser','Content-Type':'application/octet-stream'},body:part});
    if(!r.ok){let e=await r.json();throw new Error(I.message(e.detail||'Upload failed. Select the original file again to resume.'));}
    const x=await r.json();offset=x.offset;const progress=file.name+' · '+Math.round(offset/file.size*100)+'%';I.bindText($('upload-progress'),()=>progress);
   }
   await api(`/uploads/${u.id}/complete`,'POST');
  }
  localStorage.removeItem(key);await refresh();
 }I.bindText($('upload-progress'),'upload.complete');}finally{state.uploading=false;await refresh();}
}
$('new-project').onclick=()=>{$('project-dialog').showModal();$('project-input').focus();};
$('close-project').onclick=()=>{$('project-dialog').close();};
$('close-reconcile').onclick=()=>{$('reconcile-dialog').close();};
$('reconcile-resolution').onchange=syncReconcileAmount;
$('reconcile-form').onsubmit=error(async e=>{e.preventDefault();const resolution=$('reconcile-resolution').value;
 await api(`/model-calls/${$('reconcile-call-id').value}/reconcile`,'POST',{resolution,actual_cny:resolution==='BILLED'?$('reconcile-amount').value:'0',confirmation:'PROVIDER_BILLING_CHECKED',note:$('reconcile-note').value});
 $('reconcile-dialog').close();await refresh();toast(I.t(resolution==='BILLED'?'reconcile.savedBilled':'reconcile.savedNotBilled'));
});
$('project-form').onsubmit=error(async e=>{e.preventDefault();const p=await api('/projects','POST',{name:$('project-input').value,budget_cny:$('project-budget').value});$('project-dialog').close();$('project-input').value='';$('project-budget').value='300';await projects(p.id);});
$('save-budget').onclick=error(async()=>{if(!state.project)return;const cost=await api(`/projects/${state.project}/budget`,'PUT',{limit_cny:$('budget-limit').value});$('budget-limit').value=Number(cost.limit_cny);toast(I.t('budget.saved',{limit:Number(cost.limit_cny).toFixed(2)}));if(state.run)await refreshRun();});
$('project-select').onchange=error(()=>selectProject($('project-select').value));
$('run-select').onchange=error(async()=>{state.run=$('run-select').value;state.records=[];state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.kindTouched=false;await refreshRun();});
$('start').onclick=error(async()=>{if(!state.project)return;const run=await api(`/projects/${state.project}/analysis-runs`,'POST',{local_workers:Number($('local-workers').value)});state.run=run.id;state.records=[];state.recordCounts={};state.recordPagination=null;state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.kindTouched=false;await refresh();});
$('pause').onclick=error(async()=>{await api(`/analysis-runs/${state.run}/pause`,'POST');await refreshRun();});
$('resume').onclick=error(async()=>{await api(`/analysis-runs/${state.run}/resume`,'POST');state.records=[];state.recordPagination=null;state.recordsRun=null;state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;await refreshRun();});
$('file-input').onchange=error(async e=>{await uploadFiles([...e.target.files]);e.target.value='';});
$('folder-input').onchange=error(async e=>{await uploadFiles([...e.target.files]);e.target.value='';});
$('connector-provider').onchange=error(async()=>{state.connectorItems=[];renderConnectorItems();await loadConnectorStatus();});
$('connector-form').onsubmit=error(async e=>{e.preventDefault();const provider=selectedConnector(),account=$('connector-account').value.trim();
 await api(`/connectors/${provider}`,'POST',{access_token:$('connector-token').value,project_id:$('connector-project').value.trim(),hub_id:provider==='autodesk'?account:'',company_id:provider==='procore'?account:'',folder_id:$('connector-folder').value.trim(),remember:$('connector-remember').checked});
 $('connector-token').value='';await loadConnectorStatus();await listConnectorItems();});
$('connector-root').onclick=error(()=>listConnectorItems());
$('connector-disconnect').onclick=error(async()=>{const provider=selectedConnector();await api(`/connectors/${provider}`,'DELETE');state.connectorItems=[];renderConnectorItems();await loadConnectorStatus();});
$('drop-zone').ondragover=e=>{e.preventDefault();$('drop-zone').classList.add('drag');};
$('drop-zone').ondragleave=()=>{$('drop-zone').classList.remove('drag');};
$('drop-zone').ondrop=error(async e=>{e.preventDefault();$('drop-zone').classList.remove('drag');await uploadFiles([...e.dataTransfer.files]);});
$('close-drawer').onclick=()=>{$('drawer').hidden=true;};
$('filter').oninput=renderRecords;
for(const b of document.querySelectorAll('[data-kind]'))b.onclick=error(async()=>{state.kind=b.dataset.kind;state.kindTouched=true;state.records=[];state.recordPagination=null;await loadRecordPage(true);renderRecords();$('results').scrollIntoView({behavior:'smooth'});});
$('results-load-more').onclick=error(async()=>{await loadRecordPage();renderRecords();});
$('workflow-load-more').onclick=error(async()=>{await loadWorkflowPage();renderWorkflows();});
for(const fmt of ['json','xlsx'])$('export-'+fmt).onclick=()=>{if(state.run)location.href=`/api/analysis-runs/${state.run}/exports/${fmt}`;};
(async()=>{state.settings=await api('/settings');I.bindMessage($('mode'),state.settings.mode);I.bindText($('version'),()=>'v'+state.settings.version);
 I.bindText($('mode-notice'),()=>state.settings.provider==='mock'?I.t('notice.mock'):
  (state.settings.live_ready?I.t('notice.live'):I.t('notice.blocked',{reasons:state.settings.live_blockers.map(I.message).join('; ')})));
 $('vision-disclosure').hidden=!(state.settings.provider==='deepseek'&&state.settings.capabilities?.vision?.ready);
 if(state.settings.storage_warning){I.bindMessage($('environment-warning'),state.settings.storage_warning);$('environment-warning').hidden=false;}
 await loadConnectorStatus();renderConnectorItems();await projects();setInterval(()=>{if(state.run)refreshRun().catch(()=>{});},2500);
})().catch(e=>toast(e.message));
