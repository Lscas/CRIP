'use strict';
const $ = id => document.getElementById(id);
const I = window.CIRPI18n;
I.init();
document.querySelectorAll('[data-i18n-initial]').forEach(node=>I.bindText(node,node.dataset.i18nInitial));
const state = {project:null,run:null,runStatus:null,knowledge:null,projectV9Preview:null,referenceV9Preview:null,referenceKnowledge:null,referenceResults:[],referenceRuns:[],referenceComparison:null,referenceEvaluations:[],referenceEvaluationJobs:[],referenceReadiness:null,referenceScorecard:null,referenceEvaluationJobStarting:false,referenceEvaluationComparison:null,referenceEvaluationComparing:false,referenceEvaluationBusy:false,referenceEvaluationBatch:null,referenceEvaluationPendingBatch:null,referencePreviewing:false,referenceAsking:false,referenceComparing:false,referenceCases:[],referenceCasesLoaded:false,referenceCaseSelected:null,caseFlowBusy:false,caseFlowByCase:{},documents:[],documentCount:0,records:[],recordCounts:{},recordPagination:null,recordsRun:null,recordLoadPromise:null,takeoffs:[],takeoffsRun:null,workflows:[],workflowPagination:null,workflowsRun:null,workflowLoadPromise:null,connectorStatuses:[],connectorItems:[],refreshPromise:null,unresolvedCalls:[],kind:'MATERIAL',kindTouched:false,settings:null,modelSettings:null,uploading:false,asking:false};
const labels = {MATERIAL:'kind.MATERIAL',INSPECTION:'kind.INSPECTION'};
const v9Profile=id=>$(id)?.value||'';
const v9Enabled=()=>state.settings?.capabilities?.reference_layout_v9?.enabled===true;
let referenceKnowledgeFingerprint='';
function syncReferenceKnowledgeProof(){const knowledge=state.referenceKnowledge,next=JSON.stringify([state.project,knowledge?.available,knowledge?.active_update,knowledge?.run_id,knowledge?.snapshot_id,knowledge?.current]);if(referenceKnowledgeFingerprint&&referenceKnowledgeFingerprint!==next)clearV9Previews();referenceKnowledgeFingerprint=next;}
function v9ProofValid(proof,project,question,profile,knowledge){return Boolean(v9Enabled()&&proof?.proof&&proof.project===project&&proof.question===question&&proof.profile===profile&&proof.proof.run_id===knowledge?.run_id&&proof.proof.snapshot_id===knowledge?.snapshot_id);}
function v9CaseProofValid(proof,project,question,profile,runId){return Boolean(v9Enabled()&&proof?.proof&&proof.project===project&&proof.question===question&&proof.profile===profile&&proof.proof.run_id===runId);}
function syncV9Controls(){
 const enabled=v9Enabled();
 for(const id of ['project-question-profile','reference-question-profile','reference-evaluation-profile']){const node=$(id);if(!node)continue;node.closest('label').hidden=!enabled;[...node.options].forEach(option=>{if(option.value)option.hidden=!enabled;});if(!enabled)node.value='';}
}
function clearV9Previews(){state.projectV9Preview=null;state.referenceV9Preview=null;}
const modelLiveFields=$('model-remember').closest('#model-live-fields');
modelLiveFields.insertBefore($('model-vision-option'),$('model-remember').closest('label'));
modelLiveFields.insertBefore($('model-reasoning-option'),$('model-vision-option'));
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
const modelPresets={
 deepseek:{base_url:'https://api.deepseek.com',model:'deepseek-flash'},
 gemini:{base_url:'https://generativelanguage.googleapis.com/v1beta/openai',model:'gemini-3.6-flash'},
 openai:{base_url:'https://api.openai.com/v1',model:''}
};
function syncModelSettings(usePreset=false){
 const provider=$('model-provider').value,live=provider!=='mock',custom=provider==='custom',openai=provider==='openai',preset=modelPresets[provider];
 $('model-live-fields').hidden=!live;$('model-approved').required=live;$('model-remember').disabled=!live;
 const deepseek=provider==='deepseek',deepseekModel=$('model-deepseek-model').value;
 $('model-deepseek-option').hidden=!deepseek;$('model-name-option').hidden=deepseek;
 $('model-reasoning-option').hidden=!deepseek;$('model-reasoning-effort').disabled=!deepseek;
 if(!deepseek)$('model-reasoning-effort').value='none';
 const vision=$('model-vision-enabled'),visionSupported=custom||openai||(deepseek&&deepseekModel==='deepseek-flash');
 vision.disabled=!visionSupported;if(!visionSupported)vision.checked=false;
 const structured=$('model-structured-output'),structuredSupported=custom||openai;
 $('model-structured-option').hidden=!structuredSupported;
 structured.disabled=!structuredSupported;if(!structuredSupported)structured.value='json_object';
 $('model-base-url').readOnly=live&&!custom;$('model-name').readOnly=live&&!custom&&!openai;
 $('model-base-url').required=live;$('model-name').required=live&&!deepseek;$('model-deepseek-model').required=deepseek;
 if(usePreset){
  if(preset){$('model-base-url').value=preset.base_url;$('model-name').value=preset.model;if(deepseek){$('model-deepseek-model').value=preset.model;vision.checked=false;}if(openai){vision.checked=false;structured.value='json_schema';}}
  else if(custom){$('model-base-url').value='';$('model-name').value='';vision.checked=false;structured.value='json_object';}
  $('model-api-key').value='';$('model-approved').checked=false;I.bindText($('model-key-status'),'modelSettings.keyUnknown');
 }
}
async function loadModelSettings(){
 const data=await api('/model-settings');state.modelSettings=data;$('model-provider').value=data.provider;
 $('model-base-url').value=data.api_base_url||modelPresets[data.provider]?.base_url||'';
 $('model-name').value=data.model||modelPresets[data.provider]?.model||'';
 $('model-deepseek-model').value=['deepseek-v4-flash','deepseek-v4-flash-vision-exp'].includes(data.model)?'deepseek-flash':data.model||'deepseek-flash';
 $('model-reasoning-effort').value=data.reasoning_effort||'none';
 $('model-vision-enabled').checked=Boolean(data.vision_enabled);
 $('model-structured-output').value=data.structured_output_mode||'json_object';
 $('model-api-key').value='';$('model-remember').checked=Boolean(data.saved_profile||data.saved_key||data.local_model);
 $('model-approved').checked=false;I.bindText($('model-key-status'),data.saved_key?'modelSettings.keySaved':'modelSettings.keyNotSaved');
 syncModelSettings(false);$('apply-model-settings').disabled=!data.restart_supported;
}
async function waitForModelRestart(previousInstance){
 for(let attempt=0;attempt<80;attempt++){
  await new Promise(resolve=>setTimeout(resolve,500));
  try{const response=await fetch('/api/settings',{cache:'no-store'});if(response.ok){const data=await response.json();if(data.service_instance&&data.service_instance!==previousInstance){location.reload();return;}}}catch{}
 }
 throw new Error(I.t('modelSettings.restartTimeout'));
}
async function reloadForServiceChange(){
 try{
  const response=await fetch('/api/settings',{cache:'no-store'});if(!response.ok)return;
  const current=await response.json();
  if(state.settings?.service_instance&&current.service_instance!==state.settings.service_instance)location.reload();
 }catch{}
}
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
async function selectProject(id){if(state.referenceEvaluationBatch)state.referenceEvaluationBatch.stopRequested=true;state.project=id;referenceKnowledgeFingerprint='';clearV9Previews();state.run=null;state.runStatus=null;state.knowledge=null;state.referenceKnowledge=null;state.referenceResults=[];state.referenceRuns=[];state.referenceComparison=null;state.referenceEvaluations=[];state.referenceEvaluationJobs=[];state.referenceReadiness=null;state.referenceScorecard=null;state.referenceEvaluationComparison=null;state.referenceEvaluationPendingBatch=null;state.referenceCases=[];state.referenceCasesLoaded=false;state.referenceCaseSelected=null;state.documents=[];state.documentCount=0;state.records=[];state.recordCounts={};state.recordPagination=null;state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.unresolvedCalls=[];state.kindTouched=false;
 $('question-results').replaceChildren();$('reference-preview-results').replaceChildren();$('reference-comparison-results').replaceChildren();clearReferenceReadiness();clearReferenceScorecard();$('reference-evaluation-comparison-results').replaceChildren();renderReferenceResults();renderReferenceCases();renderReferenceEvaluationJobs();renderReferenceRunOptions();renderReferenceEvaluationCompareOptions();updateQuestionAvailability();updateReferenceAvailability();
 renderConnectorItems();
 if(!id){I.bindText($('project-name'),'project.none');$('reconciliation-panel').hidden=true;renderRecords();renderTakeoffs();renderWorkflows();return;}
 const p=await api('/projects/'+id);I.bindText($('project-name'),()=>p.name);
 await refresh();
}
async function refresh(){
 if(!state.project)return;
 const manifest=await api(`/projects/${state.project}/manifest`);$('files-body').replaceChildren();
 state.documents=manifest.documents;state.documentCount=manifest.documents.length;
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
 const allRuns=await api(`/projects/${state.project}/analysis-runs`);const runs=allRuns.filter(r=>(r.capabilities?.analysis_mode||'LEGACY_ANALYSIS')==='LEGACY_ANALYSIS');setReferenceRuns(allRuns);state.knowledge=await api(`/projects/${state.project}/knowledge`);state.referenceKnowledge=await api(`/projects/${state.project}/reference-knowledge`);syncReferenceKnowledgeProof();await loadReferenceResults();await loadReferenceEvaluations();$('run-select').replaceChildren(optionT('run.select'));
 runs.forEach(r=>$('run-select').append(I.bindText(new Option('',r.id),()=>r.created_at.slice(0,19).replace('T',' ')+' · '+I.status(r.status))));
 if(!state.run&&runs.length)state.run=runs[0].id;
 if(state.run){$('run-select').value=state.run;await refreshRun();}else{state.runStatus=null;updateQuestionAvailability();renderRecords();renderTakeoffs();renderWorkflows();I.bindText($('run-state'),'run.notStarted');}
 $('start').disabled=state.uploading||!manifest.documents.length||state.unresolvedCalls.length>0;updateReferenceAvailability();
}
async function refreshRun(forceRecords=false){
 if(!state.run)return;
 if(state.refreshPromise){await state.refreshPromise;if(!forceRecords)return;}
 const task=(async()=>{
   const rid=state.run;const run=await api('/analysis-runs/'+rid);
   if(rid!==state.run)return;
   const previousStatus=state.runStatus;state.runStatus=run.status;
   if(state.project&&['PARTIAL','COMPLETED'].includes(run.status)&&!['PARTIAL','COMPLETED'].includes(previousStatus||'')){
    state.knowledge=await api(`/projects/${state.project}/knowledge`);
   }
  const active=['QUEUED','RUNNING'].includes(run.status);
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
   const perf=run.performance||{},stages=Object.entries(perf).filter(([name])=>name.startsWith('stage.'));
   const elapsed=stages.reduce((sum,[,value])=>sum+value.total_ms,0)/1000,responses=perf['model.response']||{samples:0,average_ms:0};
   I.bindText($('performance'),'run.performance',{elapsed:elapsed.toFixed(1),requests:responses.samples,average:(responses.average_ms/1000).toFixed(2)});
   I.bindText($('coverage-detail'),()=>JSON.stringify({coverage:c,performance:perf,capabilities:run.capabilities},null,2));
  $('pause').disabled=!active;
   renderReconciliation(state.unresolvedCalls);
   updateQuestionAvailability();updateReferenceAvailability();
  if(state.unresolvedCalls.length)$('start').disabled=true;
  $('resume').disabled=!['PAUSED','PAUSED_PROVIDER','PAUSED_BUDGET','INTERRUPTED'].includes(run.status)||state.unresolvedCalls.length>0;
  $('export-json').disabled=active;$('export-xlsx').disabled=active;renderRecords();renderTakeoffs();renderWorkflows();
 })();
 state.refreshPromise=task;
 try{await task;}finally{if(state.refreshPromise===task)state.refreshPromise=null;}
}
function updateQuestionAvailability(){
 $('project-question-profile').disabled=state.asking||state.caseFlowBusy;
 const profile=v9Profile('project-question-profile'),knowledge=profile?state.referenceKnowledge:state.knowledge,ready=Boolean(state.project&&knowledge?.available&&!knowledge.active_update);
 const managedActive=state.referenceEvaluationJobs.some(job=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(job.state));
 $('project-question').disabled=!ready||state.asking||state.caseFlowBusy;
 const question=$('project-question').value.trim(),proof=state.projectV9Preview;
 $('project-preview').disabled=!ready||!profile||state.asking||question.length<3;
 $('ask-submit').disabled=!ready||state.asking||state.caseFlowBusy||managedActive||question.length<3||state.unresolvedCalls.length>0||Boolean(profile&&!v9ProofValid(proof,state.project,question,profile,state.referenceKnowledge));
 if(!state.asking){
  if(!state.project)I.bindText($('question-status'),'ask.selectProject');
  else if(knowledge?.active_update)I.bindText($('question-status'),'ask.updating');
  else if(!knowledge?.available)I.bindText($('question-status'),'ask.analyzeFirst');
  else I.bindText($('question-status'),knowledge.current?'ask.readySaved':'ask.readyOutdated',{date:knowledge.created_at.slice(0,19).replace('T',' ')});
 }
}
function updateReferenceAvailability(){
 $('reference-question-profile').disabled=state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||state.caseFlowBusy;
 $('reference-evaluation-profile').disabled=state.referenceEvaluationBusy||state.caseFlowBusy;
 const knowledge=state.referenceKnowledge,active=Boolean(knowledge?.active_update);
 const legacyActive=['QUEUED','RUNNING'].includes(state.runStatus||'');
 const managedActive=state.referenceEvaluationJobs.some(job=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(job.state));
 const ready=Boolean(state.project&&knowledge?.available&&!active),flowBusy=state.caseFlowBusy;
 const question=$('reference-question').value.trim();
 const evaluationQuestions=referenceEvaluationQuestions();
 $('start').disabled=state.uploading||!state.documentCount||state.unresolvedCalls.length>0||active||legacyActive||managedActive||flowBusy;
 $('reference-prepare').disabled=!state.project||!state.documentCount||active||legacyActive||managedActive||state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||flowBusy;
 $('reference-question').disabled=!ready||state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||flowBusy;
 $('reference-preview').disabled=!ready||question.length<3||state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||flowBusy;
 const profile=v9Profile('reference-question-profile'),proof=state.referenceV9Preview;
 $('reference-ask').disabled=!ready||question.length<3||managedActive||state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||flowBusy||Boolean(profile&&!v9ProofValid(proof,state.project,question,profile,state.referenceKnowledge));
 $('reference-results-refresh').disabled=!state.project||flowBusy;
 $('reference-export-json').disabled=!state.project;
 $('reference-evaluation-name').disabled=!ready||state.referenceEvaluationBusy||flowBusy;
 $('reference-evaluation-questions').disabled=!ready||state.referenceEvaluationBusy||flowBusy;
 $('reference-evaluation-create').disabled=!ready||state.referenceEvaluationBusy||flowBusy||!$('reference-evaluation-name').value.trim()||evaluationQuestions.length<1||evaluationQuestions.length>50;
 $('reference-evaluation-refresh').disabled=!state.project||state.referenceEvaluationBusy||flowBusy;
 const evaluationBaseline=$('reference-evaluation-compare-baseline').value,evaluationCandidate=$('reference-evaluation-compare-candidate').value;
 $('reference-evaluation-compare-baseline').disabled=state.referenceEvaluations.length<2||state.referenceEvaluationBusy||state.referenceEvaluationComparing||flowBusy;
 $('reference-evaluation-compare-candidate').disabled=!evaluationCandidate||state.referenceEvaluationBusy||state.referenceEvaluationComparing||flowBusy;
 $('reference-evaluation-compare').disabled=!evaluationBaseline||!evaluationCandidate||evaluationBaseline===evaluationCandidate||state.referenceEvaluationBusy||state.referenceEvaluationComparing||flowBusy;
 const baseline=$('reference-compare-baseline').value,candidate=$('reference-compare-candidate').value;
 $('reference-compare-baseline').disabled=state.referenceRuns.length<2||state.referenceComparing||flowBusy;
 $('reference-compare-candidate').disabled=state.referenceRuns.length<2||state.referenceComparing||flowBusy;
 $('reference-compare').disabled=state.referenceRuns.length<2||!baseline||!candidate||baseline===candidate||state.referenceComparing||flowBusy;
 if(!state.project)I.bindText($('reference-run-status'),'reference.selectProject');
 else if(!state.documentCount)I.bindText($('reference-run-status'),'reference.noFiles');
 else if(active)I.bindText($('reference-run-status'),'reference.runPreparing');
 else if(!knowledge?.available)I.bindText($('reference-run-status'),'reference.prepareFirst');
 else if(knowledge.current)I.bindText($('reference-run-status'),'reference.runReady',{date:knowledge.created_at.slice(0,19).replace('T',' ')});
 else I.bindText($('reference-run-status'),'reference.runOutdated',{date:knowledge.created_at.slice(0,19).replace('T',' ')});
}
async function loadReferenceResults(options={}){
 const project=state.project;if(!project){state.referenceResults=[];renderReferenceResults();return;}
 const data=await api(`/projects/${project}/reference-results?limit=50`);if(state.project!==project)return;if(options.isCurrent&&!options.isCurrent())return;
 state.referenceResults=data.items;renderReferenceResults();
}
function upsertReferenceResult(saved){const index=state.referenceResults.findIndex(value=>value.result_id===saved.result_id);if(index>=0)state.referenceResults[index]=saved;else state.referenceResults=[saved,...state.referenceResults];}
async function recoverPendingReferenceResult(caseId,project=state.project){const memory=state.caseFlowByCase[caseId],resultId=memory?.pendingResultId;if(!project||!resultId)return false;try{const saved=await api(`/reference-results/${resultId}`);if(state.project!==project||saved.result_id!==resultId||saved.project_id!==project||saved.run_id!==memory.pendingRunId||saved.question!==memory.pendingQuestion)return false;upsertReferenceResult(saved);delete memory.pendingResultId;delete memory.pendingRunId;delete memory.pendingQuestion;return true;}catch{return false;}}
function referenceEvaluationQuestions(){return $('reference-evaluation-questions').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);}
async function loadReferenceEvaluations(){
 if(!state.project){state.referenceEvaluations=[];renderReferenceEvaluations();return;}
 const data=await api(`/projects/${state.project}/reference-evaluations?limit=20`);
 state.referenceEvaluations=data.items;clearReferenceReadiness();clearReferenceScorecard();renderReferenceEvaluations();
 await loadReferenceEvaluationJobs();
}
function activeReferenceEvaluationJob(job){return ['QUEUED','RUNNING','STOP_REQUESTED'].includes(job.state);}
async function loadReferenceEvaluationJobs(){
 if(!state.project){state.referenceEvaluationJobs=[];renderReferenceEvaluationJobs();return;}
 const data=await api(`/projects/${state.project}/reference-evaluation-jobs?limit=20`);
 state.referenceEvaluationJobs=data.items;renderReferenceEvaluationJobs();updateQuestionAvailability();updateReferenceAvailability();
}
function renderReferenceEvaluationJobs(){
 const root=$('reference-evaluation-jobs');root.replaceChildren();
 if(!state.referenceEvaluationJobs.length){I.bindText($('reference-evaluation-jobs-status'),'reference.evaluationJobsEmpty');return;}
 I.bindText($('reference-evaluation-jobs-status'),'reference.evaluationJobsCount',{count:state.referenceEvaluationJobs.length});
 state.referenceEvaluationJobs.forEach(job=>{const card=el('article',null,'reference-evaluation-job'),head=el('div',null,'reference-result-head');
  head.append(el('h4',job.evaluation_name),elT('span','reference.evaluationJobState.'+job.state,{},'badge'));card.append(head);
  card.append(elT('p','reference.evaluationJobSummary',{completed:job.completed_count,total:job.pending_at_start,failed:job.failed_count,remaining:job.remaining_count}));
  card.append(elT('small','reference.evaluationMeta',{provider:job.profile.provider,model:job.profile.text_model,protocol:job.profile.api_protocol,mode:job.profile.inference_mode,format:job.profile.structured_output_mode,selector:job.selector_version}));
  if(job.current_item)card.append(elT('p','reference.evaluationJobCurrent',{number:job.current_item.ordinal+1,question:job.current_item.question}));
  if(job.reason_code!=='NONE')card.append(elT('p','reference.evaluationJobReason.'+job.reason_code,{},'muted'));
  if(activeReferenceEvaluationJob(job)){const stop=elT('button','reference.evaluationJobStop',{},'outline compact');stop.disabled=job.stop_requested;stop.onclick=error(async()=>{stop.disabled=true;const saved=await api(`/reference-evaluation-jobs/${job.job_id}/stop`,'POST');const index=state.referenceEvaluationJobs.findIndex(value=>value.job_id===saved.job_id);if(index>=0)state.referenceEvaluationJobs[index]=saved;clearReferenceReadiness();renderReferenceEvaluationJobs();updateQuestionAvailability();updateReferenceAvailability();});card.append(stop);}
  root.append(card);
 });
}
function replaceReferenceEvaluation(evaluation){
 const index=state.referenceEvaluations.findIndex(value=>value.evaluation_id===evaluation.evaluation_id);
 if(index>=0)state.referenceEvaluations[index]=evaluation;
 clearReferenceReadiness();clearReferenceScorecard();
}
function clearReferenceReadiness(){state.referenceReadiness=null;$('reference-evaluation-readiness').replaceChildren();I.bindText($('reference-evaluation-readiness-status'),'reference.evaluationReadinessIdle');}
function clearReferenceScorecard(){state.referenceScorecard=null;$('reference-evaluation-scorecard').replaceChildren();I.bindText($('reference-evaluation-scorecard-status'),'reference.evaluationScorecardIdle');}
function activeReferenceProfile(){
 const current=state.settings;if(!current)return null;
 return {provider:current.provider,text_model:current.model,
  vision_enabled:Boolean(current.capabilities?.vision?.ready),
  vision_model:current.capabilities?.vision?.ready?current.capabilities.vision.model:null,
  inference_mode:current.thinking,structured_output_mode:current.structured_output_mode,
  api_protocol:current.api_protocol};
}
function sameReferenceProfile(first,second){return Boolean(first&&second&&JSON.stringify(first)===JSON.stringify(second));}
function compatibleReferenceEvaluations(first,second){
 if(!first||!second||first.evaluation_id===second.evaluation_id||first.run_id!==second.run_id||first.snapshot_id!==second.snapshot_id||first.question_set_hash!==second.question_set_hash||first.selector_version!==second.selector_version||first.items.length!==second.items.length)return false;
 return first.items.every((item,index)=>{const other=second.items[index];return item.ordinal===other.ordinal&&item.question_key===other.question_key&&item.question===other.question;});
}
function evaluationOption(evaluation){return new Option(`${evaluation.name} · ${evaluation.profile.text_model} · ${evaluation.selector_version} · ${evaluation.status}`,evaluation.evaluation_id);}
function renderReferenceEvaluationCompareOptions(){
 const baseline=$('reference-evaluation-compare-baseline'),candidate=$('reference-evaluation-compare-candidate');
 const oldBaseline=baseline.value,oldCandidate=candidate.value;baseline.replaceChildren();candidate.replaceChildren();
 state.referenceEvaluations.forEach(item=>baseline.append(evaluationOption(item)));
 const ids=new Set(state.referenceEvaluations.map(item=>item.evaluation_id));
 baseline.value=ids.has(oldBaseline)?oldBaseline:(state.referenceEvaluations[1]?.evaluation_id||state.referenceEvaluations[0]?.evaluation_id||'');
 const selected=state.referenceEvaluations.find(item=>item.evaluation_id===baseline.value);
 const compatible=state.referenceEvaluations.filter(item=>compatibleReferenceEvaluations(selected,item));
 compatible.forEach(item=>candidate.append(evaluationOption(item)));
 const compatibleIds=new Set(compatible.map(item=>item.evaluation_id));
 candidate.value=compatibleIds.has(oldCandidate)?oldCandidate:(compatible[0]?.evaluation_id||'');
 if(state.referenceEvaluations.length<2)I.bindText($('reference-evaluation-compare-status'),'reference.evaluationCompareNeedTwo');
 else if(!compatible.length)I.bindText($('reference-evaluation-compare-status'),'reference.evaluationCompareNeedCompatible');
 else if(!state.referenceEvaluationComparison)I.bindText($('reference-evaluation-compare-status'),'reference.evaluationCompareReady');
 updateReferenceAvailability();
}
function renderEvaluationComparisonSide(value,titleKey){
 const root=el('section',null,'reference-comparison-version');root.append(elT('h5',titleKey));
 if(value.kind==='PENDING'){root.append(elT('p','reference.evaluationComparePending',{},'muted'));return root;}
 if(value.kind==='FAILURE'){root.append(elT('span','reference.evaluationItemFailed',{},'badge warn'),el('p',value.detail));return root;}
 root.append(I.bindStatus(el('span',null,'badge'),value.status));
 if(value.answer)root.append(el('p',value.answer));
 else if((value.missing||[]).length)root.append(elT('p','reference.missing',{items:value.missing.join('; ')},'muted'));
 if((value.calculations||[]).length){root.append(elT('strong','reference.calculations'));value.calculations.forEach(item=>root.append(el('small',`${item.operator}: ${(item.operands||[]).join(', ')} → ${item.result}`)));}
 root.append(elT('small','reference.compareVersionMeta',{provider:value.provider,model:value.model,basis:value.answer_basis,review:I.status(value.review.status)}));
 (value.citations||[]).forEach(citation=>{const source=el('div',null,'reference-comparison-citation');
  if(citation.quote)source.append(el('blockquote',citation.quote));else if(citation.observation)source.append(el('p',citation.observation));
  const page=citation.page_number||(citation.locator||{}).page_number;source.append(el('small',[citation.file_name,page?I.t('reference.page',{page}):''].filter(Boolean).join(' · ')));root.append(source);
 });
 root.append(elT('small','reference.evaluationCompareHash',{hash:value.outcome_hash}));return root;
}
function renderReferenceEvaluationComparison(data){
 state.referenceEvaluationComparison=data;const root=$('reference-evaluation-comparison-results');root.replaceChildren();
 root.append(elT('p','reference.evaluationCompareSummary',{total:data.summary.TOTAL,pending:data.summary.PENDING,changed:data.summary.CHANGED,unchanged:data.summary.UNCHANGED},'reference-comparison-summary'));
 const profiles=el('div',null,'reference-comparison-columns');
 [
  [data.baseline,'reference.evaluationBaseline'],
  [data.candidate,'reference.evaluationCandidate'],
 ].forEach(([evaluation,titleKey])=>{const card=el('section',null,'reference-comparison-version');card.append(elT('h5',titleKey),el('strong',evaluation.name),elT('small','reference.evaluationMeta',{provider:evaluation.profile.provider,model:evaluation.profile.text_model,protocol:evaluation.profile.api_protocol,mode:evaluation.profile.inference_mode,format:evaluation.profile.structured_output_mode,selector:evaluation.selector_version}));profiles.append(card);});
 root.append(profiles);
 data.items.forEach(item=>{const card=el('article',null,'reference-comparison-card'),head=el('div',null,'reference-result-head');head.append(el('h4',item.question),elT('span','reference.change.'+item.change,{},'badge'));card.append(head);
  if(item.review_changed)card.append(elT('span','reference.reviewChanged',{},'badge warn'));
  const sides=el('div',null,'reference-comparison-columns');sides.append(renderEvaluationComparisonSide(item.baseline,'reference.evaluationBaseline'),renderEvaluationComparisonSide(item.candidate,'reference.evaluationCandidate'));card.append(sides);root.append(card);
 });
}
function openReferenceEvaluationBatch(evaluation){
 const pending=evaluation.items.filter(item=>item.state==='PENDING');
 if(!pending.length)return;
 state.referenceEvaluationPendingBatch=evaluation.evaluation_id;
 $('reference-evaluation-run-confirm').checked=false;
 $('reference-evaluation-run-start').disabled=true;
 $('reference-evaluation-run-managed').disabled=true;
 I.bindText($('reference-evaluation-run-summary'),'reference.evaluationBatchSummary',{name:evaluation.name,pending:pending.length,decisions:pending.length*3});
 I.bindText($('reference-evaluation-run-source'),'reference.evaluationBatchSource',{run:evaluation.run_id,snapshot:evaluation.snapshot_id});
 I.bindText($('reference-evaluation-run-profile'),'reference.evaluationBatchProfile',{provider:evaluation.profile.provider,model:evaluation.profile.text_model,protocol:evaluation.profile.api_protocol,mode:evaluation.profile.inference_mode,format:evaluation.profile.structured_output_mode,selector:evaluation.selector_version,vision:I.t(evaluation.profile.vision_enabled?'reference.evaluationVisionEnabled':'reference.evaluationVisionDisabled')});
 $('reference-evaluation-run-dialog').showModal();
}
async function runReferenceEvaluationBatch(evaluationId){
 if(state.caseFlowBusy||state.referenceEvaluationBusy)return;
 state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();
 let batch=null;
 try{
  const current=await api(`/reference-evaluations/${evaluationId}`);
  replaceReferenceEvaluation(current);
  const pending=current.items.filter(item=>item.state==='PENDING');
  if(!pending.length){renderReferenceEvaluations();I.bindText($('reference-evaluation-status'),'reference.evaluationBatchNoPending');return;}
  batch={evaluationId,projectId:current.project_id,stopRequested:false,completed:0,total:pending.length,current:null};
  state.referenceEvaluationBatch=batch;renderReferenceEvaluations();
  for(const item of pending){
   if(batch.stopRequested||state.project!==batch.projectId)break;
   const unresolved=await api(`/projects/${batch.projectId}/unresolved-model-calls`);
   if(batch.stopRequested||state.project!==batch.projectId)break;
   state.unresolvedCalls=unresolved;renderReconciliation(unresolved);updateQuestionAvailability();updateReferenceAvailability();
   if(unresolved.length)throw new Error(I.t('reference.evaluationBatchUnresolved'));
   if(batch.stopRequested||state.project!==batch.projectId)break;
   batch.current=item.item_id;
   I.bindText($('reference-evaluation-status'),'reference.evaluationBatchProgress',{current:batch.completed+1,total:batch.total,number:item.ordinal+1});
   const data=await api(`/reference-evaluations/${current.evaluation_id}/items/${item.item_id}/execute`,'POST');
   replaceReferenceEvaluation(data.evaluation);batch.completed+=1;batch.current=null;
   renderReferenceEvaluations();await loadReferenceResults();
   I.bindText($('reference-evaluation-status'),data.failure?'reference.evaluationBatchFailed':'reference.evaluationBatchSaved',{completed:batch.completed,total:batch.total,number:item.ordinal+1});
  }
  if(batch.stopRequested||state.project!==batch.projectId)I.bindText($('reference-evaluation-status'),'reference.evaluationBatchStopped',{completed:batch.completed,total:batch.total});
  else I.bindText($('reference-evaluation-status'),'reference.evaluationBatchComplete',{count:batch.completed});
 }catch(e){
  if(batch)try{const unresolved=await api(`/projects/${batch.projectId}/unresolved-model-calls`);if(state.project===batch.projectId){state.unresolvedCalls=unresolved;renderReconciliation(unresolved);}}catch(_ignored){}
  toast(e.message);I.bindText($('reference-evaluation-status'),batch?'reference.evaluationBatchHalted':'reference.evaluationBatchStartFailed',{completed:batch?.completed||0,total:batch?.total||0});
 }finally{
  state.referenceEvaluationBusy=false;state.referenceEvaluationBatch=null;
  try{if(batch&&state.project===batch.projectId){await loadReferenceEvaluations();await loadReferenceResults();}}catch(e){toast(e.message);}
  renderReferenceEvaluations();updateReferenceAvailability();
 }
}
async function startManagedReferenceEvaluationJob(evaluationId){
 if(state.caseFlowBusy||state.referenceEvaluationJobStarting)return;
 state.referenceEvaluationJobStarting=true;state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-status'),'reference.evaluationJobStarting');
 try{
  const job=await api(`/reference-evaluations/${evaluationId}/jobs`,'POST',{confirmed:true});
  state.referenceEvaluationJobs=[job,...state.referenceEvaluationJobs.filter(value=>value.job_id!==job.job_id)];clearReferenceReadiness();renderReferenceEvaluationJobs();I.bindText($('reference-evaluation-status'),'reference.evaluationJobQueued',{id:job.job_id});
 }finally{state.referenceEvaluationJobStarting=false;state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateQuestionAvailability();updateReferenceAvailability();}
}
function renderReferenceReadiness(data){
 state.referenceReadiness=data;const root=$('reference-evaluation-readiness');root.replaceChildren();
 const evaluation=state.referenceEvaluations.find(item=>item.evaluation_id===data.evaluation_id);
 const card=el('article',null,'reference-readiness-card'),head=el('div',null,'reference-result-head');
 head.append(el('h4',evaluation?.name||data.evaluation_id),elT('span','reference.evaluationReadinessState.'+data.status,{},data.status==='READY'?'badge':'badge warn'));
 card.append(head,elT('p','reference.evaluationReadinessSummary',{
  questions:data.summary.question_count,pending:data.summary.pending_question_count,
  selected:data.summary.selected_question_count,pages:data.summary.unique_page_count,
  references:data.summary.total_page_references,repeated:data.summary.repeated_page_references,
  bytes:data.summary.sum_pending_selected_text_bytes,decisions:data.summary.maximum_remaining_model_decisions
 },'reference-evaluation-summary'));
 card.append(elT('p','reference.evaluationReadinessSelector',{selector:data.selector_version},'muted'));
 card.append(elT('p','reference.evaluationReadinessSafety',{},'notice'));
 if(data.blockers.length){const blockers=el('ul',null,'reference-readiness-flags');data.blockers.forEach(code=>blockers.append(elT('li','reference.evaluationReadinessBlocker.'+code)));card.append(blockers);}
 data.questions.forEach(question=>{const item=el('section',null,'reference-readiness-question'),itemHead=el('div',null,'reference-result-head');
  itemHead.append(elT('strong','reference.evaluationQuestion',{number:question.ordinal+1}),elT('span','reference.evaluationReadinessQuestionState.'+question.item_state,{},'badge'));item.append(itemHead,el('p',question.question),elT('small','reference.evaluationReadinessQuestionSummary',{pages:question.selected_page_count,bytes:question.selected_text_bytes,candidates:question.candidate_pages_considered},'muted'));
  if(question.warnings.length){const warnings=el('ul',null,'reference-readiness-flags');question.warnings.forEach(code=>warnings.append(elT('li','reference.evaluationReadinessWarning.'+code)));item.append(warnings);}
  question.source_conflicts.forEach(value=>item.append(el('p',value,'muted')));
  question.pages.forEach(page=>item.append(elT('p','reference.evaluationReadinessPage',{file:page.file_name,page:page.page_number??'—',bytes:page.selected_text_bytes,score:page.score,reasons:page.reason_codes.join(', ')||'—'},'reference-readiness-page')));
  card.append(item);
 });root.append(card);
}
async function inspectReferenceEvaluationReadiness(evaluation){
 if(state.referenceEvaluationBusy)return;
 state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-readiness-status'),'reference.evaluationReadinessInspecting');
 try{const data=await api(`/reference-evaluations/${evaluation.evaluation_id}/readiness`);renderReferenceReadiness(data);I.bindText($('reference-evaluation-readiness-status'),'reference.evaluationReadinessReady',{id:data.manifest_id});}
 finally{state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateReferenceAvailability();}
}
function renderReferenceAdjudicationHistory(root,data){
 root.replaceChildren();
 if(!data.items.length){root.append(elT('p','reference.evaluationScorecardHistoryEmpty',{},'muted'));return;}
 data.items.forEach(event=>root.append(elT('p','reference.evaluationScorecardHistoryEvent',{
  before:I.t('reference.evaluationScorecardVerdict.'+event.before.verdict),
  after:I.t('reference.evaluationScorecardVerdict.'+event.after.verdict),
  version:event.after.version,date:event.created_at.slice(0,19).replace('T',' '),
  unsupported:event.after.unsupported_claim?I.t('reference.evaluationScorecardUnsupportedYes'):I.t('reference.evaluationScorecardUnsupportedNo'),
  note:event.after.note||'—'
 },'muted')));
}
function renderReferenceScorecard(data){
 state.referenceScorecard=data;const root=$('reference-evaluation-scorecard');root.replaceChildren();
 const card=el('article',null,'reference-scorecard-card'),head=el('div',null,'reference-result-head');
 head.append(el('h4',data.name),elT('span','reference.evaluationScorecardState.'+data.status,{},data.thresholds_met?'badge':'badge warn'));
 card.append(head,elT('p','reference.evaluationScorecardSummary',{
  total:data.summary.TOTAL,terminal:data.summary.TERMINAL,valid:data.summary.CONTRACT_VALID,
  answered:data.summary.ANSWERED,disabled:data.summary.MODEL_DISABLED,
  adjudicated:data.summary.ADJUDICATED,
  usable:data.summary.FULLY_USABLE,partial:data.summary.PARTIAL,
  unusable:data.summary.UNUSABLE,unsupported:data.summary.UNSUPPORTED_CLAIM
 },'reference-evaluation-summary'));
 card.append(elT('p','reference.evaluationScorecardCriteria',{
  valid:data.criteria.contract_valid_count_required,total:data.criteria.question_count_required,
  percent:data.criteria.contract_valid_percent_required,
  usable:data.criteria.fully_usable_count_required,
  unsupported:data.criteria.unsupported_claim_count_allowed
 },'muted'),elT('p','reference.evaluationScorecardSafety',{},'notice'));
 data.items.forEach(item=>{const section=el('section',null,'reference-scorecard-question'),itemHead=el('div',null,'reference-result-head');
  const outcome=item.state==='PENDING'?elT('span','reference.evaluationScorecardOutcomePending',{},'badge warn'):item.failure_id?elT('span','reference.evaluationScorecardOutcomeFailure',{},'badge warn'):I.bindStatus(el('span',null,'badge'),item.result_status);
  itemHead.append(elT('strong','reference.evaluationQuestion',{number:item.ordinal+1}),outcome);section.append(itemHead,el('p',item.question));
  const adjudication=item.adjudication,editor=el('div',null,'reference-scorecard-editor');
  const verdict=el('select');verdict.setAttribute('aria-label',I.t('reference.evaluationScorecardVerdictLabel'));
  const allowed=item.result_status==='ANSWERED'?['FULLY_USABLE','PARTIAL','UNUSABLE']:item.state==='PENDING'?[]:['UNUSABLE'];
  const unreviewed=new Option(I.t('reference.evaluationScorecardVerdict.UNREVIEWED'),'');unreviewed.disabled=Boolean(allowed.length);verdict.append(unreviewed);
  allowed.forEach(value=>verdict.append(new Option(I.t('reference.evaluationScorecardVerdict.'+value),value)));
  verdict.value=adjudication.verdict==='UNREVIEWED'?'':adjudication.verdict;
  const unsupported=el('input');unsupported.type='checkbox';unsupported.checked=adjudication.unsupported_claim;
  const unsupportedLabel=el('label',null,'confirm-check');unsupportedLabel.append(unsupported,elT('span','reference.evaluationScorecardUnsupported'));
  const note=el('textarea');note.maxLength=2000;note.value=adjudication.note;note.setAttribute('placeholder',I.t('reference.evaluationScorecardNotePlaceholder'));note.setAttribute('aria-label',I.t('reference.evaluationScorecardNoteLabel'));
  const save=elT('button','reference.evaluationScorecardSave',{},'primary compact');save.type='button';
  const sync=()=>{const eligible=item.result_status==='ANSWERED'&&verdict.value!=='FULLY_USABLE';unsupported.disabled=!eligible;if(!eligible)unsupported.checked=false;save.disabled=!verdict.value||state.referenceEvaluationBusy;};verdict.onchange=sync;sync();
  save.onclick=error(async()=>{state.referenceEvaluationBusy=true;save.disabled=true;I.bindText($('reference-evaluation-scorecard-status'),'reference.evaluationScorecardSaving',{number:item.ordinal+1});try{const saved=await api(`/reference-evaluations/${data.evaluation_id}/items/${item.item_id}/adjudication`,'POST',{verdict:verdict.value,unsupported_claim:unsupported.checked,expected_version:adjudication.version,note:note.value});renderReferenceScorecard(saved);I.bindText($('reference-evaluation-scorecard-status'),'reference.evaluationScorecardSaved',{number:item.ordinal+1});}finally{state.referenceEvaluationBusy=false;if(state.referenceScorecard)renderReferenceScorecard(state.referenceScorecard);renderReferenceEvaluations();updateReferenceAvailability();}});
  const history=elT('button','reference.evaluationScorecardHistory',{},'outline compact'),historyRoot=el('div',null,'reference-scorecard-history');history.type='button';history.disabled=adjudication.version===0;history.onclick=error(async()=>{history.disabled=true;try{const saved=await api(`/reference-evaluations/${data.evaluation_id}/items/${item.item_id}/adjudication-history?limit=50`);renderReferenceAdjudicationHistory(historyRoot,saved);}finally{history.disabled=false;}});
  verdict.disabled=item.state==='PENDING';note.disabled=item.state==='PENDING';editor.append(verdict,unsupportedLabel,note,save,history);section.append(editor,historyRoot);card.append(section);
 });root.append(card);
}
async function inspectReferenceEvaluationScorecard(evaluation){
 if(state.referenceEvaluationBusy)return;
 state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-scorecard-status'),'reference.evaluationScorecardLoading');
 try{const data=await api(`/reference-evaluations/${evaluation.evaluation_id}/scorecard`);renderReferenceScorecard(data);I.bindText($('reference-evaluation-scorecard-status'),'reference.evaluationScorecardReady',{id:data.scorecard_id});}
 finally{state.referenceEvaluationBusy=false;if(state.referenceScorecard)renderReferenceScorecard(state.referenceScorecard);renderReferenceEvaluations();updateReferenceAvailability();}
}
function renderReferenceEvaluations(){
 const root=$('reference-evaluations');root.replaceChildren();
 renderReferenceEvaluationCompareOptions();
 if(!state.referenceEvaluations.length){root.append(elT('p','reference.evaluationEmpty',{},'muted'));return;}
 state.referenceEvaluations.forEach(evaluation=>{const card=el('article',null,'reference-evaluation-card'),head=el('div',null,'reference-result-head');
  const title=el('div');title.append(el('h4',evaluation.name),elT('small','reference.evaluationMeta',{provider:evaluation.profile.provider,model:evaluation.profile.text_model,protocol:evaluation.profile.api_protocol,mode:evaluation.profile.inference_mode,format:evaluation.profile.structured_output_mode,selector:evaluation.selector_version}));
  head.append(title,elT('span','reference.evaluationState.'+evaluation.status,{},'badge'));card.append(head,elT('p','reference.evaluationSummary',{total:evaluation.summary.TOTAL,pending:evaluation.summary.PENDING,failed:evaluation.summary.FAILED,answered:evaluation.summary.ANSWERED,accepted:evaluation.summary.ACCEPTED,rejected:evaluation.summary.REJECTED},'reference-evaluation-summary'));
  const activeBatch=state.referenceEvaluationBatch?.evaluationId===evaluation.evaluation_id;
  if(activeBatch){
   const stop=elT('button',state.referenceEvaluationBatch.stopRequested?'reference.evaluationBatchStopping':'reference.evaluationBatchStopAfterCurrent',{},'outline compact');stop.disabled=state.referenceEvaluationBatch.stopRequested;stop.onclick=()=>{state.referenceEvaluationBatch.stopRequested=true;I.bindText($('reference-evaluation-status'),'reference.evaluationBatchStopRequested');renderReferenceEvaluations();};
   const actions=el('div',null,'reference-evaluation-batch-actions');actions.append(stop);card.append(actions);
  }else if(evaluation.summary.PENDING>0){
   const managedActive=state.referenceEvaluationJobs.some(activeReferenceEvaluationJob);
   const runPending=elT('button','reference.evaluationRunPending',{count:evaluation.summary.PENDING},'primary compact');runPending.disabled=state.referenceEvaluationBusy||state.unresolvedCalls.length>0||managedActive;runPending.onclick=()=>openReferenceEvaluationBatch(evaluation);
   const actions=el('div',null,'reference-evaluation-batch-actions');actions.append(runPending,elT('small','reference.evaluationRunPendingHelp',{decisions:evaluation.summary.PENDING*3},'muted'));card.append(actions);
  }
  const cloneActions=el('div',null,'reference-evaluation-batch-actions');
  const inspect=elT('button','reference.evaluationReadinessInspect',{},'outline compact');inspect.disabled=state.referenceEvaluationBusy;inspect.onclick=error(()=>inspectReferenceEvaluationReadiness(evaluation));
  const scorecard=elT('button','reference.evaluationScorecardOpen',{},'outline compact');scorecard.disabled=state.referenceEvaluationBusy;scorecard.onclick=error(()=>inspectReferenceEvaluationScorecard(evaluation));
  const layoutBound=evaluation.selector_version==='literal-page-selector-9',cloneProfile=el('select');
  const clone=elT('button',layoutBound?'reference.v9Clone':'reference.evaluationClone',{},'outline compact');
  if(layoutBound){cloneProfile.dataset.testid='reference-v9-clone-profile';cloneProfile.append(optionT('reference.v9CloneProfile'),optionT('reference.v9FlashNone','FLASH_NONE'),optionT('reference.v9FlashLow','FLASH_LOW'),optionT('reference.v9Pro','PRO'));cloneProfile.disabled=!v9Enabled()||state.referenceEvaluationBusy;cloneProfile.onchange=()=>{clone.disabled=!v9Enabled()||state.referenceEvaluationBusy||!cloneProfile.value||cloneProfile.value===evaluation.profile.profile_id;};cloneActions.append(cloneProfile);}
  clone.disabled=state.referenceEvaluationBusy||(layoutBound?true:sameReferenceProfile(evaluation.profile,activeReferenceProfile()));
  clone.onclick=error(async()=>{const project=state.project,targetProfile=cloneProfile.value;if(layoutBound&&(!v9Enabled()||!targetProfile||targetProfile===evaluation.profile.profile_id))return;state.referenceEvaluationBusy=true;state.referenceEvaluationComparison=null;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-status'),'reference.evaluationCloning');
   try{const data=await api(`/reference-evaluations/${evaluation.evaluation_id}/clone`,'POST',layoutBound?{profile_id:targetProfile}:undefined);if(state.project!==project)return;state.referenceEvaluations=[data.evaluation,...state.referenceEvaluations];renderReferenceEvaluations();$('reference-evaluation-compare-baseline').value=evaluation.evaluation_id;renderReferenceEvaluationCompareOptions();$('reference-evaluation-compare-candidate').value=data.evaluation.evaluation_id;I.bindText($('reference-evaluation-status'),'reference.evaluationCloned',{model:data.evaluation.profile.text_model});}
   finally{state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateReferenceAvailability();}});
  cloneActions.append(inspect,scorecard,clone,elT('small','reference.evaluationManagementHelp',{},'muted'));card.append(cloneActions);
  evaluation.items.forEach(item=>{const row=el('div',null,'reference-evaluation-item'),copy=el('div');copy.append(elT('strong','reference.evaluationQuestion',{number:item.ordinal+1}),el('p',item.question));
   const actions=el('div',null,'row');
   if(item.result){actions.append(I.bindStatus(el('span',null,'badge'),item.result.status),I.bindStatus(el('span',null,'badge'),item.result.review.status),referenceCaseButton({run_id:evaluation.run_id,question:item.question,result_id:item.result.result_id,evaluation_id:evaluation.evaluation_id,question_id:item.item_id}));}
    else if(item.failure){actions.append(elT('span','reference.evaluationItemFailed',{},'badge warn'),referenceCaseButton({run_id:evaluation.run_id,question:item.question,evaluation_id:evaluation.evaluation_id,question_id:item.item_id}));if(item.failure.validator_category)copy.append(elT('p','reference.evaluationFailureCategory',{category:item.failure.validator_category},'muted'));if(item.failure.execution_receipt)copy.append(referenceExecutionReceipts([item.failure.execution_receipt]));}
   else{
    const preview=elT('button','reference.evaluationPreview',{},'outline compact');preview.disabled=state.referenceEvaluationBusy;preview.onclick=error(async()=>{state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-status'),'reference.evaluationPreviewing',{number:item.ordinal+1});try{const data=await api(`/reference-evaluations/${evaluation.evaluation_id}/items/${item.item_id}/preview`,'POST');renderReferencePreview(data);I.bindText($('reference-evaluation-status'),'reference.evaluationPreviewReady',{number:item.ordinal+1});}finally{state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateReferenceAvailability();}});
     const run=elT('button','reference.evaluationRun',{},'primary compact');run.disabled=state.referenceEvaluationBusy||state.referenceEvaluationJobs.some(activeReferenceEvaluationJob);run.onclick=error(async()=>{state.referenceEvaluationBusy=true;renderReferenceEvaluations();updateReferenceAvailability();I.bindText($('reference-evaluation-status'),'reference.evaluationRunning',{number:item.ordinal+1});try{const data=await api(`/reference-evaluations/${evaluation.evaluation_id}/items/${item.item_id}/execute`,'POST');replaceReferenceEvaluation(data.evaluation);await loadReferenceResults();I.bindText($('reference-evaluation-status'),data.failure?'reference.evaluationFailed':data.replayed?'reference.evaluationReplayed':'reference.evaluationSaved',{number:item.ordinal+1});}finally{state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateReferenceAvailability();}});
    actions.append(preview,run);
   }
   row.append(copy,actions);card.append(row);
  });root.append(card);
 });
}
function setReferenceRuns(runs){
 state.referenceRuns=(runs||[]).filter(run=>(run.capabilities?.analysis_mode||'LEGACY_ANALYSIS')==='REFERENCE_QA'&&['PARTIAL','COMPLETED'].includes(run.status));
 renderReferenceRunOptions();
}
function renderReferenceRunOptions(){
 const baseline=$('reference-compare-baseline'),candidate=$('reference-compare-candidate');
 const oldBaseline=baseline.value,oldCandidate=candidate.value;baseline.replaceChildren();candidate.replaceChildren();
 state.referenceRuns.forEach(run=>{const label=run.created_at.slice(0,19).replace('T',' ')+' · '+I.status(run.status)+' · '+run.id.slice(-8);baseline.append(new Option(label,run.id));candidate.append(new Option(label,run.id));});
 const ids=new Set(state.referenceRuns.map(run=>run.id));
 candidate.value=ids.has(oldCandidate)?oldCandidate:(state.referenceRuns[0]?.id||'');
 baseline.value=ids.has(oldBaseline)&&oldBaseline!==candidate.value?oldBaseline:(state.referenceRuns[1]?.id||state.referenceRuns[0]?.id||'');
 if(state.referenceRuns.length<2)I.bindText($('reference-compare-status'),'reference.compareNeedTwo');
 else if(!state.referenceComparison)I.bindText($('reference-compare-status'),'reference.compareReadyToRun');
 updateReferenceAvailability();
}
async function refreshReferenceWorkspace(){
 const project=state.project;if(!project)return;
 const wasActive=Boolean(state.referenceKnowledge?.active_update),knowledge=await api(`/projects/${project}/reference-knowledge`);if(state.project!==project)return;
 let runs=null;if(wasActive&&!knowledge.active_update){runs=await api(`/projects/${project}/analysis-runs`);if(state.project!==project)return;await loadReferenceResults();if(state.project!==project)return;}
 state.referenceKnowledge=knowledge;syncReferenceKnowledgeProof();if(runs)setReferenceRuns(runs);
 updateReferenceAvailability();updateQuestionAvailability();
}
function renderReferencePreview(data){
 const root=$('reference-preview-results');root.replaceChildren();const selection=data.page_selection||data;
 const v8=selection.context_policy==='COMPLETE_SELECTED_SCOPE_V1';
 root.append(elT('h3','reference.selectedPages'),v8?elT('p','reference.previewV8Summary',{
  pages:selection.anchor_page_count||selection.selected_page_count||0,expanded:selection.expanded_page_count||0},'muted'):elT('p','reference.pageSummary',{
  count:selection.selected_page_count||0,bytes:selection.byte_count||0,
  candidates:selection.candidate_pages_considered||0},'muted'));
 if(!(selection.pages||[]).length){root.append(elT('p','reference.noPages',{},'muted'));return;}
 (selection.pages||[]).forEach(page=>{const card=el('article',null,'reference-page-card');
  card.append(el('h4',[page.file_name,page.page_number?I.t('reference.page',{page:page.page_number}):''].filter(Boolean).join(' · ')),
              elT('small','reference.pageAudit',{score:page.score,terms:(page.matched_terms||[]).join(', ')||'—',reasons:(page.reason_codes||[]).join(', ')||'—'}),
              el('p',page.preview));root.append(card);
 });
}
function referenceCitation(item,result){
 const citation=item.citation||{},root=el('div',null,'question-citation');
 if(citation.type==='TEXT'){
  root.append(el('blockquote',citation.quote));const locator=citation.locator||{};
  root.append(el('small',[citation.file_name,locator.page_number?I.t('reference.page',{page:locator.page_number}):'',locator.section,locator.paragraph].filter(Boolean).join(' · ')));
  const open=elT('button','reference.source',{},'link evidence-button');
  open.onclick=error(()=>showEvidence(citation.evidence_id,null,result.run_id));root.append(open);
 }else if(citation.type==='IMAGE_REGION'){
  root.append(el('p',citation.observation),el('small',[citation.file_name,I.t('reference.page',{page:citation.page_number}),I.t('reference.hash',{hash:citation.image_sha256})].filter(Boolean).join(' · ')));
  const open=elT('a','reference.pageImage',{},'link evidence-button');open.href=`/api/analysis-runs/${result.run_id}/documents/${citation.document_id}/pages/${citation.page_number}/image`;open.target='_blank';open.rel='noopener';root.append(open);
 }
 return root;
}
function referenceExecutionReceipts(receipts){
 receipts=(receipts||[]).filter(Boolean);if(!receipts.length)return null;
 const details=el('details',null,'reference-execution-receipts');details.append(elT('summary','reference.executionReceipts',{count:receipts.length}));
 details.append(elT('p','reference.executionReceiptSafe',{},'muted'));
 receipts.forEach(receipt=>{const entry=el('div',null,'reference-execution-receipt');entry.append(
  elT('small','reference.executionReceiptMeta',{round:receipt.round,model:receipt.model,protocol:receipt.api_protocol,format:receipt.structured_output_mode,evidence:receipt.evidence_count,bytes:receipt.system_text_bytes+receipt.user_text_bytes,images:receipt.image_bytes}),
  elT('small','reference.executionReceiptIds',{call:receipt.model_call_id,request:receipt.request_hash,prompt:receipt.prompt_contract_hash}));details.append(entry);});
 return details;
}
function caseAttachmentPicker(selected=[]){
 const picker=el('select',null,'reference-case-attachments');picker.multiple=true;picker.size=Math.min(5,Math.max(2,state.documents.length||2));
 state.documents.forEach(document=>{const option=new Option(document.name,document.id);option.selected=selected.includes(document.id);picker.append(option);});return picker;
}
function selectedAttachments(picker){return [...picker.selectedOptions].map(option=>option.value);}
function caseFlowBlocked(){
 return state.caseFlowBusy||state.uploading||state.unresolvedCalls.length>0||
  Boolean(state.referenceKnowledge?.active_update)||['QUEUED','RUNNING'].includes(state.runStatus||'')||
  state.referenceAsking||state.referencePreviewing||state.referenceEvaluationBusy||state.asking||
  state.referenceEvaluationJobs.some(job=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(job.state));
}
function sameOpenCase(project,caseId){return state.project===project&&state.referenceCaseSelected?.case_id===caseId;}
async function loadReferenceCases(){
 const project=state.project;if(!project){state.referenceCases=[];state.referenceCasesLoaded=false;renderReferenceCases();return;}
 const status=$('reference-case-filter-status').value,query=status?`?status=${encodeURIComponent(status)}&limit=100`:'?limit=100';
 const data=await api(`/projects/${project}/reference-cases${query}`);if(state.project!==project)return;
 state.referenceCases=data.items;state.referenceCasesLoaded=true;renderReferenceCases();
}
async function openReferenceCase(caseId){
 const project=state.project,data=await api(`/reference-cases/${caseId}`);if(state.project!==project)return;
 state.referenceCaseSelected=data;renderReferenceCaseDetail();await recoverPendingReferenceResult(caseId,project);if(state.project===project&&state.referenceCaseSelected?.case_id===caseId)renderReferenceCaseDetail();
}
function renderReferenceCases(){
 const root=$('reference-cases');root.replaceChildren();$('reference-case-detail').replaceChildren();
 if(!state.project){I.bindText($('reference-case-status'),'reference.casesSelectProject');return;}
 if(!state.referenceCasesLoaded){I.bindText($('reference-case-status'),'reference.casesIdle');return;}
 I.bindText($('reference-case-status'),'reference.casesCount',{count:state.referenceCases.length});
 if(!state.referenceCases.length){root.append(elT('p','reference.casesEmpty',{},'muted'));return;}
 state.referenceCases.forEach(item=>{const card=el('article',null,'reference-case-card'),head=el('div',null,'reference-result-head'),copy=el('div');
  copy.append(el('h4',item.question),I.bindStatus(el('span',null,'badge'),item.source.status),elT('small','reference.caseMeta',{assignee:item.assignee||I.t('reference.caseUnassigned'),note:item.latest_note||'—'}));
  const open=elT('button','reference.caseOpen',{},'outline compact');open.onclick=error(()=>openReferenceCase(item.case_id));head.append(copy,open);card.append(head);root.append(card);
 });
}
function renderReferenceCaseDetail(){
 const root=$('reference-case-detail');root.replaceChildren();const item=state.referenceCaseSelected,renderedProject=state.project;if(!item)return;
 const source=item.source||{},projectionCase=source.result_kind==='PROJECTION_LOOP_OUTCOME',legacyCase=source.result_kind==null||source.result_kind==='REFERENCE_QA_RESULT';
 const card=el('article',null,'reference-case-detail-card');card.append(el('h3',item.question),I.bindStatus(el('span',null,'badge'),source.status||item.status));
 card.append(elT('p','reference.caseSource',{run:item.run_id,snapshot:item.snapshot_id},'muted'));
 const form=el('form',null,'reference-case-form'),status=el('select'),assignee=el('input'),note=el('textarea'),resolution=el('textarea'),supplemental=el('textarea'),attachments=caseAttachmentPicker((item.attachments||[]).map(value=>value.document_id));
 ['OPEN','NEEDS_INFORMATION','IN_REVIEW','RESOLVED'].forEach(value=>status.append(I.bindStatus(new Option('',value),value)));status.value=item.status;
 assignee.value=item.assignee||'';assignee.maxLength=240;note.maxLength=4000;resolution.maxLength=4000;resolution.value=item.resolution||'';supplemental.maxLength=1000;
 const field=(key,control)=>{const label=el('label');label.append(elT('span',key),control);return label;};
 form.append(field('reference.caseStatus',status),field('reference.caseAssignee',assignee),field('reference.caseNote',note),field('reference.caseResolution',resolution),field('reference.caseSupplemental',supplemental),field('reference.caseAttachments',attachments));
 const help=elT('p','reference.caseReopenHelp',{},'muted');const save=elT('button','reference.caseSave',{},'primary');save.type='submit';form.append(help,save);
 form.onsubmit=error(async event=>{event.preventDefault();const project=renderedProject,caseId=item.case_id;if(!sameOpenCase(project,caseId)||caseFlowBlocked())return;state.caseFlowBusy=true;save.disabled=true;updateReferenceAvailability();
  try{const saved=await api(`/reference-cases/${caseId}/update`,'POST',{expected_version:item.version,status:status.value,assignee:assignee.value,note:note.value,resolution:resolution.value,attachments:selectedAttachments(attachments),supplemental_question:supplemental.value});if(!sameOpenCase(project,caseId))return;state.referenceCaseSelected=saved;await loadReferenceCases();const fresh=await api(`/reference-cases/${caseId}`);if(sameOpenCase(project,caseId)){state.referenceCaseSelected=fresh;toast(I.t('reference.caseSaved'));}}finally{state.caseFlowBusy=false;save.disabled=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });card.append(form);
 if(!projectionCase&&!legacyCase){const attachmentNames=(item.attachments||[]).map(value=>value.name).join(', ')||I.t('reference.caseNoAttachments');card.append(elT('p','reference.caseSourceWorkflowUnavailable',{},'muted'),elT('p','reference.caseAttachmentList',{names:attachmentNames},'muted'));const history=el('details',null,'reference-case-history');history.append(elT('summary','reference.caseHistory',{count:(item.history||[]).length}));(item.history||[]).forEach(event=>history.append(elT('p','reference.caseHistoryEvent',{action:event.action,actor:event.actor,note:event.note||'—'})));card.append(history);root.append(card);return;}
 const flow=el('section',null,'reference-case-followup-flow'),memory=state.caseFlowByCase[item.case_id]||{selectedIds:[],preparedRunId:null,preview:null};state.caseFlowByCase[item.case_id]=memory;
 const currentCase=()=>state.referenceCaseSelected?.case_id===item.case_id?state.referenceCaseSelected:item;
 const blocked=()=>currentCase().status==='RESOLVED'||caseFlowBlocked();
 flow.append(elT('h4','reference.caseFlowTitle'),elT('p',item.status==='RESOLVED'?'reference.caseFlowReopen':'reference.caseFlowHelp',{},'muted'));
 if(projectionCase)flow.append(elT('p','reference.caseProjectionLegacyQuestion',{},'notice'));
 const stagedAttachments=caseAttachmentPicker(memory.selectedIds);stagedAttachments.onchange=()=>{memory.selectedIds=selectedAttachments(stagedAttachments);memory.preview=null;memory.preparedRunId=null;sync();};flow.append(elT('p','reference.caseAttachments',{},'muted'),stagedAttachments);
 const uploadInput=el('input');uploadInput.type='file';uploadInput.multiple=true;uploadInput.onchange=()=>sync();const uploadButton=elT('button','reference.caseFlowUpload',{},'outline');uploadButton.type='button';
 const prepare=elT('button','reference.caseFlowPrepare',{},'outline');prepare.type='button';const refreshRuns=elT('button','reference.caseFlowRefreshRuns',{},'outline compact');refreshRuns.type='button';const runPicker=el('select');const profileLabel=el('label'),profile=el('select');profile.dataset.testid='reference-case-v9-profile';profile.append(optionT('reference.v8Default'),optionT('reference.v9FlashNone','FLASH_NONE'),optionT('reference.v9FlashLow','FLASH_LOW'),optionT('reference.v9Pro','PRO'));profile.value=memory.profile||'';profileLabel.append(elT('span','reference.v9Profile'),profile);profileLabel.hidden=!v9Enabled();const previewButton=elT('button','reference.caseFlowPreview',{},'outline');previewButton.type='button';const answer=elT('button','reference.caseFlowAsk',{},'primary');answer.type='button';
 const prepared=()=>state.referenceRuns.find(run=>run.id===memory.preparedRunId&&memory.preparedForCaseVersion===currentCase().version&&JSON.stringify(memory.preparedSelectedIds||[])===JSON.stringify(memory.selectedIds)&&['PARTIAL','COMPLETED'].includes(run.status)&&run.id!==item.run_id&&memory.selectedIds.every(id=>(run.document_ids||[]).includes(id)));
 const renderPreparedRun=()=>{const run=prepared();runPicker.replaceChildren();if(run)runPicker.append(new Option(`${run.id} · ${I.status(run.status)}`,run.id));return run;};
 const sync=()=>{const run=prepared(),preview=memory.preview;if(!v9Enabled()){profile.value='';memory.profile='';}profileLabel.hidden=!v9Enabled();uploadInput.disabled=blocked();uploadButton.disabled=blocked()||!uploadInput.files.length;stagedAttachments.disabled=blocked();prepare.disabled=blocked()||!memory.selectedIds.length;refreshRuns.disabled=state.caseFlowBusy||state.uploading||!state.project;runPicker.disabled=blocked()||!run;profile.disabled=blocked()||state.caseFlowBusy||!run;previewButton.disabled=runPicker.disabled;answer.disabled=blocked()||!preview||preview.runId!==run?.id||preview.version!==currentCase().version||preview.ids.join(',')!==memory.selectedIds.join(',')||(profile.value&&!v9CaseProofValid(preview,renderedProject,item.question,profile.value,run?.id));};
 uploadButton.onclick=error(async()=>{const project=renderedProject,caseId=item.case_id;if(!sameOpenCase(project,caseId)||blocked()||!uploadInput.files.length)return;const version=currentCase().version;state.caseFlowBusy=true;updateReferenceAvailability();sync();
  try{const uploaded=await uploadFiles([...uploadInput.files],{projectId:project,refreshAfter:false,shouldContinue:()=>sameOpenCase(project,caseId)});if(!sameOpenCase(project,caseId))return;const ids=[...new Set((uploaded||[]).map(value=>value.document_id).filter(Boolean))];if(!ids.length)throw new Error(I.t('reference.caseFlowUploadMissing'));const attachedIds=new Set((currentCase().attachments||[]).map(value=>value.document_id)),newIds=ids.filter(id=>!attachedIds.has(id)),previousSelected=memory.selectedIds.join(',');memory.selectedIds=[...new Set([...memory.selectedIds,...ids])];const selectedChanged=previousSelected!==memory.selectedIds.join(',');
   if(newIds.length){const saved=await api(`/reference-cases/${caseId}/update`,'POST',{expected_version:version,attachments:newIds,note:I.t('reference.caseFlowUploadNote')});if(!sameOpenCase(project,caseId))return;memory.preview=null;memory.preparedRunId=null;state.referenceCaseSelected=saved;}
   else if(selectedChanged){memory.preview=null;memory.preparedRunId=null;}
   state.documents=[...state.documents,...ids.filter(id=>!state.documents.some(document=>document.id===id)).map(id=>({id,name:id}))];stagedAttachments.replaceChildren(...state.documents.map(document=>{const option=new Option(document.name,document.id);option.selected=memory.selectedIds.includes(document.id);return option;}));
   if(newIds.length){await loadReferenceCases();await loadReferenceResults();const fresh=await api(`/reference-cases/${caseId}`);if(sameOpenCase(project,caseId))state.referenceCaseSelected=fresh;toast(I.t('reference.caseFlowUploaded'));}
   else toast(I.t('reference.caseFlowAlreadyAttached'));
  }finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });flow.append(uploadInput,uploadButton);
 prepare.onclick=error(async()=>{const project=renderedProject,caseId=item.case_id;if(!sameOpenCase(project,caseId)||blocked()||!memory.selectedIds.length)return;state.caseFlowBusy=true;updateReferenceAvailability();sync();
  try{const run=await api(`/projects/${project}/analysis-runs`,'POST',{local_workers:Number($('local-workers').value),analysis_mode:'REFERENCE_QA'});if(!sameOpenCase(project,caseId))return;memory.preparedRunId=run.id;memory.preparedForCaseVersion=currentCase().version;memory.preparedSelectedIds=[...memory.selectedIds];memory.preview=null;state.referenceKnowledge={...(state.referenceKnowledge||{}),active_update:true,active_run_id:run.id};toast(I.t('reference.caseFlowPrepared',{run:run.id}));
  }finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });flow.append(prepare);
 refreshRuns.onclick=error(async()=>{const project=renderedProject,caseId=item.case_id;if(!sameOpenCase(project,caseId)||state.caseFlowBusy||state.uploading||!project)return;state.caseFlowBusy=true;updateReferenceAvailability();sync();try{const [runs,knowledge]=await Promise.all([api(`/projects/${project}/analysis-runs`),api(`/projects/${project}/reference-knowledge`)]);if(!sameOpenCase(project,caseId))return;setReferenceRuns(runs);state.referenceKnowledge=knowledge;renderPreparedRun();}finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}});flow.append(refreshRuns);
 runPicker.onchange=()=>{memory.preview=null;sync();};profile.onchange=()=>{memory.profile=profile.value;memory.preview=null;sync();};
 previewButton.onclick=error(async()=>{const project=renderedProject,caseId=item.case_id,run=prepared();if(!sameOpenCase(project,caseId)||blocked()||!run)return;const runId=run.id,ids=[...memory.selectedIds],version=currentCase().version;state.caseFlowBusy=true;updateReferenceAvailability();sync();
  try{const selectedProfile=profile.value;if(selectedProfile&&!v9Enabled())return;const data=await api(`/projects/${project}/questions-v3/preview`,'POST',selectedProfile?{run_id:runId,question:item.question,selector_version:'literal-page-selector-9',profile_id:selectedProfile}:{run_id:runId,question:item.question});if(!sameOpenCase(project,caseId)||profile.value!==selectedProfile)return;const selection=data.page_selection||data,pages=selection.pages||[];if(!selection.selection_id||!ids.every(id=>pages.some(page=>page.document_id===id)))throw new Error(I.t('reference.caseFlowPreviewMissing'));memory.preview={caseId,version,runId,question:item.question,ids,selectionId:selection.selection_id,project,profile:selectedProfile,proof:data.preview_proof};renderReferencePreview(data);toast(I.t('reference.caseFlowPreviewReady'));}finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });flow.append(runPicker,profileLabel,previewButton);
 answer.onclick=error(async()=>{const project=renderedProject,caseId=item.case_id,proof=memory.preview,preparedRun=prepared();if(!sameOpenCase(project,caseId)||blocked()||!proof||!preparedRun||proof.runId!==preparedRun.id||proof.question!==item.question||proof.profile!==profile.value||(proof.profile&&!v9CaseProofValid(proof,project,item.question,proof.profile,preparedRun.id)))return;const runId=proof.runId,ids=[...proof.ids];state.caseFlowBusy=true;updateReferenceAvailability();sync();
  try{const current=await api(`/reference-cases/${caseId}`),run=await api(`/analysis-runs/${runId}`);if(!sameOpenCase(project,caseId)||current.version!==proof.version||current.status==='RESOLVED'||!['PARTIAL','COMPLETED'].includes(run.status)||run.id!==memory.preparedRunId||!ids.every(id=>(run.document_ids||[]).includes(id))||Date.parse(run.created_at||0)<=Date.parse(current.updated_at||0)){memory.preview=null;throw new Error(I.t('reference.caseFlowStale'));}
   if(!proof.profile){const freshPreview=await api(`/projects/${project}/questions-v3/preview`,'POST',{run_id:runId,question:item.question});const freshSelection=freshPreview.page_selection||freshPreview;if(!sameOpenCase(project,caseId)||freshSelection.selection_id!==proof.selectionId||!ids.every(id=>(freshSelection.pages||[]).some(page=>page.document_id===id))){memory.preview=null;throw new Error(I.t('reference.caseFlowStale'));}}
   memory.preview=null;sync();const result=await api(`/projects/${project}/questions-v3`,'POST',proof.profile?{run_id:runId,question:item.question,selector_version:'literal-page-selector-9',profile_id:proof.profile,preview_proof:proof.proof}:{run_id:runId,question:item.question});if(!sameOpenCase(project,caseId))return;
   memory.pendingResultId=result.result_id;memory.pendingRunId=runId;memory.pendingQuestion=item.question;await recoverPendingReferenceResult(caseId,project);if(!sameOpenCase(project,caseId))return;
   let linkedCase;
   try{linkedCase=await api(`/reference-cases/${caseId}/follow-up-results`,'POST',{expected_version:proof.version,run_id:runId,result_id:result.result_id,supplemental_document_ids:ids,note:I.t('reference.caseFlowLinkNote')});}
   catch(linkError){if(!sameOpenCase(project,caseId))return;try{const fresh=await api(`/reference-cases/${caseId}`);if(!sameOpenCase(project,caseId))return;state.referenceCaseSelected=fresh;if((fresh.followups||[]).some(followup=>followup.result_id===result.result_id))linkedCase=fresh;else{renderReferenceCaseDetail();toast(I.t('reference.caseFlowLinkUnknown'));return;}}catch{if(sameOpenCase(project,caseId))renderReferenceCaseDetail();toast(I.t('reference.caseFlowLinkUnknown'));return;}}
   if(!sameOpenCase(project,caseId))return;state.referenceCaseSelected=linkedCase;renderReferenceCaseDetail();toast(I.t('reference.caseFlowLinked'));
   try{await loadReferenceCases();await loadReferenceResults();const fresh=await api(`/reference-cases/${caseId}`);if(sameOpenCase(project,caseId)){state.referenceCaseSelected=fresh;renderReferenceCaseDetail();}}
   catch{if(sameOpenCase(project,caseId))toast(I.t('reference.caseFlowLinkedRefreshPending'));}
  }finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });flow.append(answer);renderPreparedRun();sync();card.append(flow);
 const candidates=(state.referenceResults||[]).filter(result=>savedFollowupCandidate(item,result));
 const followupForm=el('form',null,'reference-case-form reference-case-followup-form'),followupResult=el('select'),followupDocuments=el('select'),followupNote=el('textarea');
 followupDocuments.multiple=true;followupDocuments.size=Math.min(5,Math.max(2,(item.attachments||[]).length||2));
 (item.attachments||[]).forEach(document=>followupDocuments.append(new Option(document.name,document.document_id)));
 if(candidates.length)candidates.forEach(result=>followupResult.append(new Option(`${result.run_id} · ${result.status}`,result.result_id)));
 else followupResult.append(new Option(I.t('reference.caseFollowupNoResult'),''));
 followupNote.maxLength=4000;followupNote.setAttribute('data-i18n-placeholder','reference.caseFollowupNote');I.applyElement(followupNote);
 const followupField=(key,control)=>{const label=el('label');label.append(elT('span',key),control);return label;};
 followupForm.append(followupField('reference.caseFollowupResult',followupResult),followupField('reference.caseFollowupDocuments',followupDocuments),followupField('reference.caseFollowupNote',followupNote));
 const followupHelp=elT('p','reference.caseFollowupHelp',{},'muted'),link=elT('button','reference.caseFollowupLink',{},'outline');link.type='submit';link.disabled=!candidates.length||item.status==='RESOLVED';followupForm.append(followupHelp,link);
 followupForm.onsubmit=error(async event=>{event.preventDefault();const project=renderedProject,caseId=item.case_id;if(!sameOpenCase(project,caseId)||caseFlowBlocked()||!followupResult.value)return;const selected=(state.referenceResults||[]).find(result=>result.result_id===followupResult.value);if(!selected||!savedFollowupCandidate(item,selected))return;state.caseFlowBusy=true;link.disabled=true;updateReferenceAvailability();
  try{const saved=await api(`/reference-cases/${caseId}/follow-up-results`,'POST',{expected_version:item.version,run_id:selected.run_id,result_id:selected.result_id,supplemental_document_ids:selectedAttachments(followupDocuments),note:followupNote.value});if(!sameOpenCase(project,caseId))return;state.referenceCaseSelected=saved;await loadReferenceCases();const fresh=await api(`/reference-cases/${caseId}`);if(sameOpenCase(project,caseId)){state.referenceCaseSelected=fresh;toast(I.t('reference.caseFollowupLinked'));}}finally{state.caseFlowBusy=false;link.disabled=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
 });card.append(followupForm);
 if((item.followups||[]).length){const followups=el('details',null,'reference-case-history');followups.append(elT('summary','reference.caseFollowups',{count:item.followups.length}));item.followups.forEach(followup=>{const proof=(followup.proof||{}).documents||[];followups.append(elT('p','reference.caseFollowupMeta',{run:followup.run_id,status:followup.result_status},'muted'));if(proof.length)followups.append(elT('p','reference.caseFollowupHistoricalProof',{},'muted'));proof.forEach(document=>followups.append(elT('p','reference.caseFollowupProof',{document:document.document_id,text:document.text_input_count||0,visual:document.visual_input_count||0,citations:document.citation_count||0},'muted')));});card.append(followups);}
 if(source.result_id){if(projectionCase){if(source.status==='REVIEW_REQUIRED'&&projectionCaseAvailable())card.append(projectionCaseSourceButton(item,card));}else {const sourceButton=elT('button','reference.caseViewSource',{},'link evidence-button');sourceButton.onclick=error(async()=>{const project=state.project,result=await api(`/reference-results/${source.result_id}`);if(state.project!==project)return;const citations=el('div',null,'reference-case-sources');(result.citations||[]).forEach(citation=>citations.append(referenceCitation(citation,result)));card.append(citations);sourceButton.remove();});card.append(sourceButton);}}
 const attachmentNames=(item.attachments||[]).map(value=>value.name).join(', ')||I.t('reference.caseNoAttachments');card.append(elT('p','reference.caseAttachmentList',{names:attachmentNames},'muted'));
 const history=el('details',null,'reference-case-history');history.append(elT('summary','reference.caseHistory',{count:(item.history||[]).length}));(item.history||[]).forEach(event=>{history.append(elT('p','reference.caseHistoryEvent',{action:event.action,actor:event.actor,note:event.note||'—'}));const oldResolution=event.before?.resolution,newResolution=event.after?.resolution;if(oldResolution||newResolution)history.append(elT('p','reference.caseHistoryResolution',{before:oldResolution||'—',after:newResolution||'—'},'muted'));});card.append(history);root.append(card);
}
async function createReferenceCase(source){
 if(!state.project||caseFlowBlocked())return;const project=state.project;state.caseFlowBusy=true;updateReferenceAvailability();try{const created=await api(`/projects/${project}/reference-cases`,'POST',source);if(state.project!==project)return;await loadReferenceCases();await openReferenceCase(created.case_id);toast(I.t('reference.caseCreated'));}finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
}
function referenceCaseButton(source){const button=elT('button','reference.sendToHuman',{},'outline compact');button.onclick=error(()=>createReferenceCase(source));return button;}
function projectionReviewAvailable(){const capability=state.settings?.capabilities?.reference_projection_review;return capability?.available===true&&capability.view_version==='reference-projection-review-view-1';}
function projectionCaseAvailable(){const capability=state.settings?.capabilities?.reference_projection_review;return projectionReviewAvailable()&&capability?.case_workflow_available===true&&capability?.case_view_version==='reference-case-view-2'&&capability?.saved_followup_link_available===true&&capability?.question_creation_available===false;}
function projectionCaseIdentity(item,project=state.project){return {case_id:item.case_id,version:item.version,project_id:item.project_id||project,run_id:item.run_id,snapshot_id:item.snapshot_id,question:item.question,result_id:item.source?.result_id,result_hash:item.source?.result_hash};}
function projectionCaseIdentityCurrent(identity,card){const item=state.referenceCaseSelected,source=item?.source||{};return Boolean(card?.isConnected===true&&projectionCaseAvailable()&&state.project===identity.project_id&&item&&item.case_view_version==='reference-case-view-2'&&item.case_id===identity.case_id&&item.version===identity.version&&item.project_id===identity.project_id&&item.run_id===identity.run_id&&item.snapshot_id===identity.snapshot_id&&item.question===identity.question&&source.status==='REVIEW_REQUIRED'&&source.result_kind==='PROJECTION_LOOP_OUTCOME'&&source.result_id===identity.result_id&&source.result_hash===identity.result_hash);}
function projectionCaseResultIdentityCurrent(item,card){const current=state.referenceResults.find(value=>value.result_id===item.result_id);return Boolean(card?.isConnected===true&&projectionCaseAvailable()&&item.project_id===state.project&&current&&current.project_id===state.project&&current.result_kind==='PROJECTION_LOOP_OUTCOME'&&['REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'].includes(current.status)&&current.result_id===item.result_id&&current.result_hash===item.result_hash&&current.run_id===item.run_id&&current.snapshot_id===item.snapshot_id&&current.question===item.question);}
function savedFollowupCandidate(item,result){const sourceKind=item.source?.result_kind,resultKind=result?.result_kind,projectionSource=sourceKind==='PROJECTION_LOOP_OUTCOME',projectionResult=resultKind==='PROJECTION_LOOP_OUTCOME',legacyResult=resultKind==null||resultKind==='REFERENCE_QA_RESULT';if(!(item.project_id===state.project&&result?.project_id===state.project&&result.run_id!==item.run_id&&result.question===item.question)||(!projectionSource&&sourceKind!=null&&sourceKind!=='REFERENCE_QA_RESULT')||!legacyResult&&!projectionResult||((projectionSource||projectionResult)&&(!projectionCaseAvailable()||item.case_view_version!=='reference-case-view-2')))return false;return projectionResult?['REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'].includes(result.status):['ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT'].includes(result.status);}
function projectionReviewBox(box,width,height){return Array.isArray(box)&&box.length===4&&box.every(Number.isFinite)&&box[0]>=0&&box[1]>=0&&box[2]>=box[0]&&box[3]>=box[1]&&box[2]<=width&&box[3]<=height;}
function projectionReviewSelectionMatches(selection,index){
 const geometry=selection?.geometry,keys=['coordinate_system','page_width','page_height','rotation','bbox','render_crop_bbox','render_mode'];
 return Boolean(selection&&selection.ordinal===index+1&&typeof selection.document_name==='string'&&typeof selection.text==='string'&&Number.isInteger(selection.page_number)&&selection.page_number>=1&&Array.isArray(selection.part_refs)&&selection.part_refs.length&&selection.part_refs.every(part=>typeof part==='string'&&part)&&geometry&&Object.keys(geometry).length===keys.length&&keys.every(key=>Object.hasOwn(geometry,key))&&geometry.coordinate_system==='pdf-cropbox-points-top-left'&&Number.isFinite(geometry.page_width)&&geometry.page_width>0&&Number.isFinite(geometry.page_height)&&geometry.page_height>0&&[0,90,180,270].includes(geometry.rotation)&&projectionReviewBox(geometry.bbox,geometry.page_width,geometry.page_height)&&(geometry.render_crop_bbox===null||projectionReviewBox(geometry.render_crop_bbox,geometry.page_width,geometry.page_height))&&['SOURCE_CROP','FULL_PAGE'].includes(geometry.render_mode));
}
function projectionReviewIdentityCurrent(item,card){
 const current=state.referenceResults.find(value=>value.result_id===item.result_id);
 return Boolean(card?.isConnected===true&&projectionReviewAvailable()&&current&&current.result_kind==='PROJECTION_LOOP_OUTCOME'&&current.status==='REVIEW_REQUIRED'&&current.result_id===item.result_id&&current.result_hash===item.result_hash&&current.run_id===item.run_id&&current.snapshot_id===item.snapshot_id&&current.question===item.question);
}
function projectionReviewIdentityMatches(item,view,project,card){
 return Boolean(project===state.project&&projectionReviewIdentityCurrent(item,card)&&projectionReviewViewMatches(item,view));
}
function projectionReviewViewMatches(item,view){return Boolean(view&&view.view_version==='reference-projection-review-view-1'&&view.projection_execution_version==='reference-projection-execution-2'&&view.loop_version==='project-projection-loop-2'&&view.result_id===item.result_id&&view.result_hash===item.result_hash&&view.run_id===item.run_id&&view.snapshot_id===item.snapshot_id&&view.question===item.question&&view.result_kind==='PROJECTION_LOOP_OUTCOME'&&view.status==='REVIEW_REQUIRED'&&view.object_condition_relations_verified===false&&view.answer_completeness_verified===false&&Array.isArray(view.selections)&&view.selections.length>=1&&view.selections.length<=4&&view.selections.every(projectionReviewSelectionMatches));}
function projectionReviewView(item,view,identityCurrent=()=>projectionReviewIdentityCurrent(item,item._projectionCard)){
 const panel=el('section',null,'projection-review-view');
 panel.append(elT('h5','reference.projectionSourceTitle'));
 panel.append(elT('p','reference.projectionSourceWarning',{},'notice'));
 view.selections.forEach(selection=>{
  const source=el('article',null,'projection-review-source');
  source.append(elT('h6','reference.projectionSourceHeading',{ordinal:selection.ordinal,document:selection.document_name,page:selection.page_number}));
  source.append(elT('small','reference.projectionSourceParts',{parts:selection.part_refs.join(', ')}));
  source.append(el('p',selection.text,'projection-review-text'));
  const image=elT('button','reference.projectionSourceImage',{},'link evidence-button');
  image.onclick=()=>{
   if(image.dataset.loaded==='true'||state.project!==item._projectionProject||!identityCurrent())return;
   image.dataset.loaded='true';image.disabled=true;I.bindText(image,'reference.projectionSourceImageLoading');
   const preview=el('img',null,'projection-review-image');preview.alt=I.t('reference.projectionSourceImageAlt',{ordinal:selection.ordinal});
   preview.onload=()=>{if(state.project===item._projectionProject&&identityCurrent())image.replaceWith(preview);};
   preview.onerror=()=>{if(state.project===item._projectionProject&&identityCurrent()){image.disabled=true;I.bindText(image,'reference.projectionSourceImageUnavailable');}};
   preview.src=`/api/reference-results/${encodeURIComponent(item.result_id)}/projection-review/sources/${selection.ordinal}/image`;
  };
  source.append(image);panel.append(source);
 });
 return panel;
}
function projectionReviewButton(item,card){
 const button=elT('button','reference.projectionSourceOpen',{},'outline');
 button.onclick=error(async()=>{
  const project=state.project,identity={result_id:item.result_id,result_hash:item.result_hash,run_id:item.run_id,snapshot_id:item.snapshot_id,question:item.question};
  button.disabled=true;I.bindText(button,'reference.projectionSourceLoading');
  try{
   const view=await api(`/reference-results/${encodeURIComponent(item.result_id)}/projection-review`);
   if(!projectionReviewIdentityMatches(identity,view,project,card))return;
   const safeItem={...identity,_projectionProject:project,_projectionCard:card};
   card.append(projectionReviewView(safeItem,view));button.remove();
  }catch(err){
   if(state.project===project){I.bindText(button,'reference.projectionSourceUnavailable');button.disabled=true;}
  }
 });
 return button;
}
function projectionCaseSourceButton(caseItem,card){
 const identity=projectionCaseIdentity(caseItem),source={result_id:identity.result_id,result_hash:identity.result_hash,run_id:identity.run_id,snapshot_id:identity.snapshot_id,question:identity.question};
 const button=elT('button','reference.caseViewSource',{},'link evidence-button');
 button.onclick=error(async()=>{
  if(!projectionCaseIdentityCurrent(identity,card))return;
  button.disabled=true;I.bindText(button,'reference.projectionSourceLoading');
  try{
   const view=await api(`/reference-results/${encodeURIComponent(identity.result_id)}/projection-review`);
   if(!projectionCaseIdentityCurrent(identity,card)||!projectionReviewViewMatches(source,view))return;
   const safeItem={...source,_projectionProject:identity.project_id,_projectionCard:card};
   card.append(projectionReviewView(safeItem,view,()=>projectionCaseIdentityCurrent(identity,card)));button.remove();
  }catch(err){if(projectionCaseIdentityCurrent(identity,card)){I.bindText(button,'reference.projectionSourceUnavailable');button.disabled=true;}}
 });
 return button;
}
function projectionCaseButton(item,card){
 const source={run_id:item.run_id,question:item.question,result_id:item.result_id};
 const button=elT('button','reference.projectionSendToHuman',{},'outline compact');
 button.onclick=error(async()=>{if(!projectionCaseResultIdentityCurrent(item,card))return;await createReferenceCase(source);});
 return button;
}
function renderReferenceResults(){
 const root=$('reference-results');root.replaceChildren();
 if(!state.referenceResults.length){root.append(elT('p','reference.noResults',{},'muted'));return;}
 state.referenceResults.forEach(item=>{const card=el('article',null,'reference-result-card'),head=el('div',null,'reference-result-head');
  const title=el('div');title.append(el('h4',item.question),elT('small','reference.resultMeta',{provider:item.provider,model:item.model,date:item.created_at.slice(0,19).replace('T',' ')}));
  if(Object.hasOwn(item,'result_kind')&&item.result_kind!=='REFERENCE_QA_RESULT'){
   head.append(title,elT('span','reference.nonAnswerRecord',{},'badge warn'));card.append(head);
   if(item.result_kind==='PROJECTION_LOOP_OUTCOME'&&item.status==='REVIEW_REQUIRED'&&projectionReviewAvailable())card.append(projectionReviewButton(item,card));
   else card.append(elT('p','reference.resultWorkflowUnavailable',{},'muted'));
   if(item.result_kind==='PROJECTION_LOOP_OUTCOME'&&['REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'].includes(item.status)&&projectionCaseAvailable())card.append(projectionCaseButton(item,card));
   root.append(card);return;
  }
  const badges=el('div',null,'row');badges.append(I.bindStatus(el('span',null,'badge'),item.status),I.bindStatus(el('span',null,'badge '+(item.review.status==='PENDING'?'warn':'')),item.review.status));head.append(title,badges);card.append(head);
  if(item.result.answer)card.append(el('p',item.result.answer,'reference-answer'));
  else if((item.result.missing||[]).length)card.append(elT('p','reference.missing',{items:item.result.missing.join('; ')},'muted'));
  (item.citations||[]).forEach(citation=>card.append(referenceCitation(citation,item)));
  const receipts=referenceExecutionReceipts(item.result.execution_receipts);if(receipts)card.append(receipts);
  if(item.status==='ANSWERED'){
   const note=el('input');note.maxLength=2000;note.setAttribute('data-i18n-placeholder','reference.note');note.setAttribute('data-i18n-aria-label','reference.note');I.applyElement(note);
   const actions=el('div',null,'row reference-review-actions');
   [['ACCEPTED','reference.accept','primary'],['REJECTED','reference.reject','outline']].forEach(([action,key,cls])=>{const button=elT('button',key,{},cls);button.onclick=error(async()=>{button.disabled=true;await api(`/reference-results/${item.result_id}/review`,'POST',{action,expected_version:item.review.version,note:note.value});await loadReferenceResults();await loadReferenceEvaluations();toast(I.t('reference.reviewed'));});actions.append(button);});
   card.append(note,actions);
  }
  card.append(referenceCaseButton({run_id:item.run_id,question:item.question,result_id:item.result_id}));
  root.append(card);
 });
}
function renderComparisonSide(versions,titleKey){
 const root=el('section',null,'reference-comparison-version');root.append(elT('h5',titleKey));
 if(!versions.length){root.append(elT('p','reference.noVersion',{},'muted'));return root;}
 versions.forEach(version=>{const entry=el('article',null,'reference-comparison-entry');entry.append(I.bindStatus(el('span',null,'badge'),version.status));
  if(version.answer)entry.append(el('p',version.answer));
  else if((version.missing||[]).length)entry.append(elT('p','reference.missing',{items:version.missing.join('; ')},'muted'));
  if((version.calculations||[]).length){entry.append(elT('strong','reference.calculations'));version.calculations.forEach(item=>entry.append(el('small',`${item.operator}: ${(item.operands||[]).join(', ')} → ${item.result}`)));}
  entry.append(elT('small','reference.compareVersionMeta',{provider:version.provider,model:version.model,basis:version.answer_basis,review:I.status(version.review.status)}));
  (version.citations||[]).forEach(citation=>{const source=el('div',null,'reference-comparison-citation');
   if(citation.quote)source.append(el('blockquote',citation.quote));else if(citation.observation)source.append(el('p',citation.observation));
   const page=citation.page_number||(citation.locator||{}).page_number;
   source.append(el('small',[citation.file_name,page?I.t('reference.page',{page}):''].filter(Boolean).join(' · ')));entry.append(source);
  });
  entry.append(elT('small','reference.compareHashes',{result:version.result_hash,outcome:version.outcome_hash}));root.append(entry);
 });return root;
}
function renderReferenceComparison(data){
 state.referenceComparison=data;const root=$('reference-comparison-results');root.replaceChildren();
 root.append(elT('p','reference.compareSummary',{total:data.summary.TOTAL,added:data.summary.ADDED,removed:data.summary.REMOVED,changed:data.summary.CHANGED,unchanged:data.summary.UNCHANGED},'reference-comparison-summary'));
 if(!data.items.length){root.append(elT('p','reference.compareEmpty',{},'muted'));return;}
 data.items.forEach(item=>{const card=el('article',null,'reference-comparison-card'),head=el('div',null,'reference-result-head');head.append(el('h4',item.question),elT('span','reference.change.'+item.change,{},'badge'));card.append(head);
  const flags=el('div',null,'row');if(item.review_changed)flags.append(elT('span','reference.reviewChanged',{},'badge warn'));if(item.processing_changed)flags.append(elT('span','reference.processingChanged',{},'badge'));if(flags.childNodes.length)card.append(flags);
  const versions=el('div',null,'reference-comparison-columns');versions.append(renderComparisonSide(item.baseline_versions,'reference.baseline'),renderComparisonSide(item.candidate_versions,'reference.candidate'));card.append(versions);
  if(item.baseline_versions.length>1||item.candidate_versions.length>1)card.append(elT('small','reference.multipleVersions',{baseline:item.baseline_versions.length,candidate:item.candidate_versions.length},'muted'));
  root.append(card);
 });
}
function citationLocation(item){
 const locator=item.locator||{},parts=[item.file_name];
 if(locator.sheet)parts.push('Sheet '+locator.sheet);
 if(locator.page)parts.push('Page '+locator.page);
 if(locator.section)parts.push(String(locator.section));
 if(locator.paragraph)parts.push('Paragraph '+locator.paragraph);
 if(locator.cell)parts.push('Cell '+locator.cell);
 if(locator.text_line_start)parts.push('Lines '+locator.text_line_start+(locator.text_line_end&&locator.text_line_end!==locator.text_line_start?'–'+locator.text_line_end:''));
 return parts.filter(Boolean).join(' · ');
}
const questionSourceLabels={SPECIFICATION:'Specification',RFI:'RFI',SUBMITTAL:'Submittal',EMAIL:'Email',OTHER:'Other source'};
const questionBasisKeys={
 WORKFLOW_INDEX:'ask.basis.workflowIndex',LOCAL_PROJECT_EVIDENCE:'ask.basis.localEvidence',
 RETRIEVAL_ONLY:'ask.basis.retrievalOnly',NO_MATCHING_EVIDENCE:'ask.basis.noEvidence',
 ANALYSIS_INCOMPLETE:'ask.basis.analysisIncomplete'
};
function questionBasisKey(data){
 if(data.answer_basis==='MODEL_PROJECT_EVIDENCE')return data.cached?'ask.basis.modelCached':'ask.basis.modelLive';
 return questionBasisKeys[data.answer_basis];
}
function questionCitation(item,data){
 const source=el('div',null,'question-citation'),quote=el('blockquote',item.quote),location=el('small',citationLocation(item));
 const open=elT('button','ask.source',{},'link evidence-button');
 open.onclick=error(()=>showEvidence(item.evidence_id,{start:item.start,end:item.end},data.run_id));
 source.append(quote,location,open);return source;
}
function renderQuestionAnswer(question,data){
 const article=el('article',null,'question-answer'),basisKey=questionBasisKey(data);article.append(el('h3',question));
 if(basisKey)article.append(elT('div',basisKey,{},'question-basis'));
 article.append(el('p',data.answer));
 const workflowStatuses=[...(data.workflow_statuses||[]),...(data.workflow_conflicts||[])];
 workflowStatuses.forEach(item=>{
  const conflict=el('section',null,'question-finding');conflict.append(el('h4',item.label));
  (item.statuses||[]).forEach(status=>{
   const sources=el('div',null,'question-citation');sources.append(el('strong',workflowCodeLabel('status',status.status)));
   (status.sources||[]).forEach(source=>{const row=el('div',null,'question-conflict-source'),a=el('a',source.file_name,'link evidence-button');a.href='/api/documents/'+source.document_id+'/file';a.target='_blank';a.rel='noopener';row.append(a,el('small',I.t('workflowClassification.source.'+source.classification_source)));if(source.citation)row.append(questionCitation(source.citation,data));sources.append(row);});
   conflict.append(sources);
  });
  article.append(conflict);
 });
 const findings=data.source_findings||[],findingCitations=new Set();
 findings.forEach(item=>{
  const finding=el('section',null,'question-finding');
  finding.append(el('h4',(questionSourceLabels[item.source_type]||'Source')+' · '+item.file_name),el('p',item.statement));
  (item.citations||[]).forEach(citation=>{
   findingCitations.add(citation.evidence_id+'\n'+citation.quote);
   finding.append(questionCitation(citation,data));
  });
  article.append(finding);
 });
 const citations=(data.citations||[]).filter(item=>!findingCitations.has(item.evidence_id+'\n'+item.quote));
 if(!workflowStatuses.length&&!findings.length&&!citations.length)article.append(elT('p','ask.noCitation',{},'muted'));
 citations.forEach(item=>article.append(questionCitation(item,data)));
 $('question-results').prepend(article);
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
  const text=el('div');text.append(el('strong',call.model),elT('small','reconcile.call',{id:call.id,run:call.run_id}),el('small',diagnosticText(call)));
  const button=elT('button','reconcile.open',{},'outline');button.disabled=call.state!=='UNKNOWN';button.onclick=()=>openReconciliation(call);
  card.append(text,button);list.append(card);
 });
}
function openReconciliation(call){
 $('reconcile-form').reset();$('reconcile-call-id').value=call.id;$('reconcile-resolution').value='NOT_BILLED';$('reconcile-amount').value='0';syncReconcileAmount();
 I.bindText($('reconcile-call-summary'),()=>I.t('reconcile.summary',{model:call.model,diagnostic:diagnosticText(call)}));
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
  if(r.kind==='MATERIAL'&&c.quantity)review.append(elT('small','record.quantityReview',{status:I.status(r.quantity_review||'PENDING')}));
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
   if(c.quantity){body.append(el('p','Quantity: '+c.quantity.value+' '+c.quantity.unit));
    body.append(elT('p','record.quantityReview',{status:I.status(r.quantity_review||'PENDING')}));}
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
 if(r.kind==='MATERIAL'&&c.quantity){
  const quantityActions=el('div',null,'row');
  [['VERIFIED','record.quantityVerify'],['REJECTED','record.quantityReject'],['PENDING','record.quantityPending']].forEach(([quantity_action,key])=>{
   const button=elT('button',key,{},'outline');button.onclick=error(async()=>{
    button.disabled=true;try{await api(`/records/${r.meta.record_id}/review`,'POST',{quantity_action,expected_version:row.review_version,note:note.value});
     $('drawer').hidden=true;await refreshRun(true);toast(I.t('record.quantitySaved'));}finally{button.disabled=false;}
   });quantityActions.append(button);
  });body.append(quantityActions);
 }
  sourceIds(c).forEach((id,index)=>{const b=elT('button','record.sourceNumber',{number:index+1},'link evidence-button');b.onclick=error(()=>showEvidence(id));body.append(b);});
 const history=elT('button','record.history',{},'outline');history.onclick=error(async()=>{const data=await api(`/records/${r.meta.record_id}/history`);body.append(el('pre',JSON.stringify(data,null,2)));});body.append(history);
}
async function showEvidence(id,range=null,runId=state.run){
 const data=await api(`/analysis-runs/${runId}/evidence/${encodeURIComponent(id)}`);const e=data.evidence;
 $('drawer').hidden=false;I.bindText($('drawer-title'),e.extraction_method==='VISION'?'evidence.visionTitle':'evidence.title');const body=$('drawer-body');body.replaceChildren();
 body.append(el('h3',data.file_name),I.bindText(el('p',null,'muted'),()=>I.t('evidence.revision',{date:e.internal_revision_date||I.t('evidence.unknownDate')})));
 const location=citationLocation({file_name:null,locator:e.locator});if(location)body.append(el('p',location,'muted'));
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

async function uploadFiles(files,{projectId=state.project,refreshAfter=true,shouldContinue=()=>true}={}){
 if(!projectId){toast('Create or select a project first');return [];}
 if(state.uploading){toast('An upload is already in progress');return;}
 const uploaded=[];state.uploading=true;$('start').disabled=true;
 try{for(const file of files){
  const key=['cirp-upload',projectId,file.webkitRelativePath||file.name,file.size,file.lastModified].join(':');
  let u;const saved=localStorage.getItem(key);
  if(saved){try{u=await api('/uploads/'+saved);}catch{localStorage.removeItem(key);}}
  if(u&&['COMPLETE','DUPLICATE'].includes(u.state)&&u.document_id)uploaded.push(u);
  if(!shouldContinue())return uploaded;
  if(!u||u.state==='ABORTED'){u=await api(`/projects/${projectId}/uploads`,'POST',{name:file.name,size:file.size});localStorage.setItem(key,u.id);}
  if(!shouldContinue())return uploaded;
  if(!['COMPLETE','DUPLICATE'].includes(u.state)){
   // Verify accepted chunks so a replaced same-name file cannot mix content.
   for(const chunk of u.chunks||[]){
    const digest=await crypto.subtle.digest('SHA-256',await file.slice(chunk.offset,chunk.offset+chunk.size).arrayBuffer());
    const hex=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hex!==chunk.checksum){localStorage.removeItem(key);await api(`/uploads/${u.id}/abort`,'POST');throw new Error('The resumed file contents changed. Select the file again to upload.');}
   }
   for(let offset=u.offset;offset<file.size;){
    if(!shouldContinue())return uploaded;
    const part=file.slice(offset,offset+state.settings.upload_chunk_bytes);
    const r=await fetch(`/api/uploads/${u.id}/chunk?offset=${offset}`,{method:'PUT',headers:{'X-CIRP-Client':'browser','Content-Type':'application/octet-stream'},body:part});
    if(!r.ok){let e=await r.json();throw new Error(I.message(e.detail||'Upload failed. Select the original file again to resume.'));}
    const x=await r.json();offset=x.offset;const progress=file.name+' · '+Math.round(offset/file.size*100)+'%';I.bindText($('upload-progress'),()=>progress);
   }
   if(!shouldContinue())return uploaded;const complete=await api(`/uploads/${u.id}/complete`,'POST');if(!shouldContinue())return uploaded;
   if(complete?.document_id)uploaded.push(complete);
  }
  localStorage.removeItem(key);
 }I.bindText($('upload-progress'),'upload.complete');}finally{state.uploading=false;if(refreshAfter&&state.project===projectId)await refresh();}
 return uploaded;
}
$('new-project').onclick=()=>{$('project-dialog').showModal();$('project-input').focus();};
$('close-project').onclick=()=>{$('project-dialog').close();};
$('model-settings-open').onclick=error(async()=>{await loadModelSettings();$('model-settings').scrollIntoView({behavior:'smooth'});history.replaceState(null,'','#model-settings');});
$('model-settings-nav').onclick=error(loadModelSettings);
$('model-provider').onchange=()=>syncModelSettings(true);
$('model-deepseek-model').onchange=()=>syncModelSettings(false);
$('model-settings-form').onsubmit=error(async event=>{event.preventDefault();if(caseFlowBlocked())return;state.caseFlowBusy=true;updateReferenceAvailability();const apply=$('apply-model-settings'),provider=$('model-provider').value,previous=state.settings.service_instance;apply.disabled=true;
 try{
  await api('/model-settings','POST',{provider,api_base_url:$('model-base-url').value.trim(),model:provider==='deepseek'?$('model-deepseek-model').value:$('model-name').value.trim(),api_key:$('model-api-key').value,vision_enabled:$('model-vision-enabled').checked,structured_output_mode:$('model-structured-output').value,reasoning_effort:provider==='deepseek'?$('model-reasoning-effort').value:'none',remember:$('model-remember').checked,approved:provider==='mock'||$('model-approved').checked});
  $('model-api-key').value='';toast(I.t('modelSettings.restarting'));await waitForModelRestart(previous);
 }finally{state.caseFlowBusy=false;apply.disabled=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}
});
$('close-reconcile').onclick=()=>{$('reconcile-dialog').close();};
$('reconcile-resolution').onchange=syncReconcileAmount;
$('reconcile-form').onsubmit=error(async e=>{e.preventDefault();const resolution=$('reconcile-resolution').value;
 await api(`/model-calls/${$('reconcile-call-id').value}/reconcile`,'POST',{resolution,actual_cny:resolution==='BILLED'?$('reconcile-amount').value:'0',confirmation:'PROVIDER_BILLING_CHECKED',note:$('reconcile-note').value});
 $('reconcile-dialog').close();await refresh();toast(I.t(resolution==='BILLED'?'reconcile.savedBilled':'reconcile.savedNotBilled'));
});
$('project-form').onsubmit=error(async e=>{e.preventDefault();const p=await api('/projects','POST',{name:$('project-input').value});$('project-dialog').close();$('project-input').value='';await projects(p.id);});
$('project-select').onchange=error(()=>selectProject($('project-select').value));
$('run-select').onchange=error(async()=>{state.run=$('run-select').value;clearV9Previews();state.runStatus=null;state.records=[];state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.kindTouched=false;$('question-results').replaceChildren();updateQuestionAvailability();await refreshRun();});
$('start').onclick=error(async()=>{if(caseFlowBlocked()||!state.project)return;state.caseFlowBusy=true;updateReferenceAvailability();try{const run=await api(`/projects/${state.project}/analysis-runs`,'POST',{local_workers:Number($('local-workers').value)});state.run=run.id;state.runStatus=null;state.records=[];state.recordCounts={};state.recordPagination=null;state.recordsRun=null;state.takeoffs=[];state.takeoffsRun=null;state.workflows=[];state.workflowPagination=null;state.workflowsRun=null;state.kindTouched=false;$('question-results').replaceChildren();updateQuestionAvailability();await refresh();}finally{state.caseFlowBusy=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();}});
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
$('project-question').oninput=()=>{state.projectV9Preview=null;updateQuestionAvailability();};
$('project-question-profile').onchange=()=>{state.projectV9Preview=null;updateQuestionAvailability();};
$('reference-question-profile').onchange=()=>{state.referenceV9Preview=null;updateReferenceAvailability();};
$('reference-evaluation-profile').onchange=updateReferenceAvailability;
$('project-preview').onclick=error(async()=>{const project=state.project,question=$('project-question').value.trim(),profile=v9Profile('project-question-profile'),runId=state.referenceKnowledge?.run_id,snapshotId=state.referenceKnowledge?.snapshot_id;if(!v9Enabled()||!project||!question||!profile||!runId||!snapshotId)return;const data=await api(`/projects/${project}/questions-v3/preview`,'POST',{question,run_id:runId,selector_version:'literal-page-selector-9',profile_id:profile});if(state.project!==project||state.referenceKnowledge?.run_id!==runId||state.referenceKnowledge?.snapshot_id!==snapshotId||$('project-question').value.trim()!==question||v9Profile('project-question-profile')!==profile)return;state.projectV9Preview={project,question,profile,proof:data.preview_proof};updateQuestionAvailability();});
// Capture-phase guards protect legacy handlers even if a concurrent case flow
// changes state after their disabled attributes were last rendered.
for(const guarded of ['start','reference-prepare','reference-preview','reference-evaluation-run-managed']){
 $(guarded).addEventListener('click',event=>{if(state.caseFlowBusy){event.preventDefault();event.stopImmediatePropagation();}},true);
}
for(const guarded of ['question-form','reference-evaluation-run-form','reference-evaluation-form']){
 $(guarded).addEventListener('submit',event=>{if(state.caseFlowBusy){event.preventDefault();event.stopImmediatePropagation();}},true);
}
$('reference-evaluations').addEventListener('click',event=>{if(state.caseFlowBusy&&event.target.closest('button')){event.preventDefault();event.stopImmediatePropagation();}},true);
$('question-form').onsubmit=error(async event=>{event.preventDefault();if(!state.project)return;
 const project=state.project,question=$('project-question').value.trim(),profile=v9Profile('project-question-profile'),proof=state.projectV9Preview,knowledge=profile?state.referenceKnowledge:state.knowledge;if(!knowledge?.available||(profile&&!v9ProofValid(proof,project,question,profile,state.referenceKnowledge)))return;state.asking=true;updateQuestionAvailability();I.bindText($('question-status'),'ask.searching');
 const current=()=>v9ProofValid(proof,state.project,$('project-question').value.trim(),v9Profile('project-question-profile'),state.referenceKnowledge);
 try{
  const result=profile?await api(`/projects/${state.project}/questions-v3`,'POST',{run_id:proof.proof.run_id,question,selector_version:'literal-page-selector-9',profile_id:profile,preview_proof:proof.proof}):await api(`/projects/${state.project}/questions`,'POST',{question});
  if(profile&&!current()){if(state.project===project)toast(I.t('reference.v9ScopeChanged'));return;}renderQuestionAnswer(question,result);$('project-question').value='';state.projectV9Preview=null;await refreshRun();
 }finally{state.asking=false;updateQuestionAvailability();}
});
$('reference-question').oninput=()=>{state.referenceV9Preview=null;updateReferenceAvailability();};
$('reference-evaluation-name').oninput=updateReferenceAvailability;
$('reference-evaluation-questions').oninput=updateReferenceAvailability;
$('reference-evaluation-run-confirm').onchange=()=>{const disabled=!$('reference-evaluation-run-confirm').checked;$('reference-evaluation-run-start').disabled=disabled;$('reference-evaluation-run-managed').disabled=disabled;};
$('reference-evaluation-run-cancel').onclick=()=>{state.referenceEvaluationPendingBatch=null;$('reference-evaluation-run-dialog').close();};
$('reference-evaluation-run-dialog').addEventListener('cancel',()=>{state.referenceEvaluationPendingBatch=null;});
$('reference-evaluation-run-form').onsubmit=error(async event=>{event.preventDefault();if(!$('reference-evaluation-run-confirm').checked)return;const evaluationId=state.referenceEvaluationPendingBatch;if(!evaluationId)return;state.referenceEvaluationPendingBatch=null;$('reference-evaluation-run-dialog').close();await runReferenceEvaluationBatch(evaluationId);});
$('reference-evaluation-run-managed').onclick=error(async()=>{if(!$('reference-evaluation-run-confirm').checked)return;const evaluationId=state.referenceEvaluationPendingBatch;if(!evaluationId)return;state.referenceEvaluationPendingBatch=null;$('reference-evaluation-run-dialog').close();await startManagedReferenceEvaluationJob(evaluationId);});
$('reference-prepare').onclick=error(async()=>{if(!state.project)return;state.referencePreviewing=true;updateReferenceAvailability();I.bindText($('reference-status'),'reference.preparing');
 try{
  const run=await api(`/projects/${state.project}/analysis-runs`,'POST',{local_workers:Number($('local-workers').value),analysis_mode:'REFERENCE_QA'});
  state.referenceKnowledge={...(state.referenceKnowledge||{}),active_update:true,active_run_id:run.id};syncReferenceKnowledgeProof();$('reference-preview-results').replaceChildren();I.bindText($('reference-status'),'reference.preparing');
 }finally{state.referencePreviewing=false;updateReferenceAvailability();}
});
$('reference-preview').onclick=error(async()=>{if(!state.project||!state.referenceKnowledge?.available)return;const project=state.project,question=$('reference-question').value.trim(),profile=v9Profile('reference-question-profile');state.referencePreviewing=true;updateReferenceAvailability();I.bindText($('reference-status'),'reference.previewing');
 try{const runId=state.referenceKnowledge?.run_id,snapshotId=state.referenceKnowledge?.snapshot_id;if(profile&&(!v9Enabled()||!runId||!snapshotId))return;const result=profile?await api(`/projects/${project}/questions-v3/preview`,'POST',{question,run_id:runId,selector_version:'literal-page-selector-9',profile_id:profile}):await api(`/projects/${state.project}/questions-v3/preview`,'POST',{question});if(state.project!==project||(profile&&(state.referenceKnowledge?.run_id!==runId||state.referenceKnowledge?.snapshot_id!==snapshotId))||$('reference-question').value.trim()!==question||v9Profile('reference-question-profile')!==profile)return;if(profile)state.referenceV9Preview={project,question,profile,proof:result.preview_proof};renderReferencePreview(result);I.bindText($('reference-status'),'reference.previewReady');}
 finally{state.referencePreviewing=false;updateReferenceAvailability();}
});
$('reference-question-form').onsubmit=error(async event=>{event.preventDefault();if(caseFlowBlocked()||!state.project||!state.referenceKnowledge?.available)return;const project=state.project,question=$('reference-question').value.trim(),profile=v9Profile('reference-question-profile'),proof=state.referenceV9Preview;if(profile&&!v9ProofValid(proof,project,question,profile,state.referenceKnowledge))return;state.referenceAsking=true;updateReferenceAvailability();I.bindText($('reference-status'),'reference.asking');
 const current=()=>v9ProofValid(proof,state.project,$('reference-question').value.trim(),v9Profile('reference-question-profile'),state.referenceKnowledge);
 try{const result=profile?await api(`/projects/${project}/questions-v3`,'POST',{question,run_id:proof.proof.run_id,selector_version:'literal-page-selector-9',profile_id:profile,preview_proof:proof.proof}):await api(`/projects/${state.project}/questions-v3`,'POST',{question});if(profile&&!current()){if(state.project===project)I.bindText($('reference-status'),'reference.v9ScopeChanged');return;}await loadReferenceResults(profile?{isCurrent:current}:{});if(profile&&!current()){if(state.project===project)I.bindText($('reference-status'),'reference.v9ScopeChanged');return;}I.bindText($('reference-status'),'reference.saved',{status:I.status(result.status)});$('reference-question').value='';state.referenceV9Preview=null;$('reference-preview-results').replaceChildren();}
 finally{state.referenceAsking=false;updateReferenceAvailability();}
});
$('reference-results-refresh').onclick=error(loadReferenceResults);
$('reference-cases-refresh').onclick=error(loadReferenceCases);
$('reference-case-filter-status').onchange=()=>{state.referenceCasesLoaded=false;state.referenceCases=[];state.referenceCaseSelected=null;renderReferenceCases();};
$('reference-evaluation-refresh').onclick=error(loadReferenceEvaluations);
$('reference-evaluation-compare-baseline').onchange=()=>{state.referenceEvaluationComparison=null;$('reference-evaluation-comparison-results').replaceChildren();renderReferenceEvaluationCompareOptions();};
$('reference-evaluation-compare-candidate').onchange=updateReferenceAvailability;
$('reference-evaluation-compare').onclick=error(async()=>{if(!state.project)return;const baseline=$('reference-evaluation-compare-baseline').value,candidate=$('reference-evaluation-compare-candidate').value;if(!baseline||!candidate||baseline===candidate)return;state.referenceEvaluationComparing=true;updateReferenceAvailability();I.bindText($('reference-evaluation-compare-status'),'reference.evaluationComparing');
 try{const query=new URLSearchParams({baseline_evaluation_id:baseline,candidate_evaluation_id:candidate});const result=await api(`/projects/${state.project}/reference-evaluations/compare?${query}`);renderReferenceEvaluationComparison(result);I.bindText($('reference-evaluation-compare-status'),'reference.evaluationCompared',{id:result.comparison_id});}
 finally{state.referenceEvaluationComparing=false;updateReferenceAvailability();}
});
$('reference-evaluation-form').onsubmit=error(async event=>{event.preventDefault();if(!state.project||!state.referenceKnowledge?.available)return;const project=state.project,questions=referenceEvaluationQuestions(),profile=v9Profile('reference-evaluation-profile');if(profile&&!v9Enabled())return;state.referenceEvaluationBusy=true;updateReferenceAvailability();renderReferenceEvaluations();I.bindText($('reference-evaluation-status'),'reference.evaluationCreating');
 const name=$('reference-evaluation-name').value.trim(),runId=state.referenceKnowledge.run_id,snapshotId=state.referenceKnowledge.snapshot_id;
 try{const created=await api(`/projects/${project}/reference-evaluations`,'POST',{run_id:runId,name,questions,...(profile?{selector_version:'literal-page-selector-9',profile_id:profile}:{})});if(state.project!==project)return;if(profile&&(!v9Enabled()||v9Profile('reference-evaluation-profile')!==profile||state.referenceKnowledge?.run_id!==runId||state.referenceKnowledge?.snapshot_id!==snapshotId||$('reference-evaluation-name').value.trim()!==name||JSON.stringify(referenceEvaluationQuestions())!==JSON.stringify(questions))){I.bindText($('reference-evaluation-status'),'reference.v9ScopeChanged');return;}state.referenceEvaluations=[created,...state.referenceEvaluations];$('reference-evaluation-name').value='';$('reference-evaluation-questions').value='';I.bindText($('reference-evaluation-status'),'reference.evaluationCreated',{count:created.summary.TOTAL});}
 finally{state.referenceEvaluationBusy=false;renderReferenceEvaluations();updateReferenceAvailability();}
});
$('reference-export-json').onclick=()=>{if(state.project)location.href=`/api/projects/${state.project}/reference-results/export.json`;};
$('reference-compare-baseline').onchange=updateReferenceAvailability;
$('reference-compare-candidate').onchange=updateReferenceAvailability;
$('reference-compare').onclick=error(async()=>{if(!state.project)return;const baseline=$('reference-compare-baseline').value,candidate=$('reference-compare-candidate').value;if(!baseline||!candidate||baseline===candidate)return;state.referenceComparing=true;updateReferenceAvailability();I.bindText($('reference-compare-status'),'reference.comparing');
 try{const query=new URLSearchParams({baseline_run_id:baseline,candidate_run_id:candidate});const result=await api(`/projects/${state.project}/reference-results/compare?${query}`);renderReferenceComparison(result);I.bindText($('reference-compare-status'),'reference.compareReady',{id:result.comparison_id});}
 finally{state.referenceComparing=false;updateReferenceAvailability();}
});
for(const fmt of ['json','xlsx'])$('export-'+fmt).onclick=()=>{if(state.run)location.href=`/api/analysis-runs/${state.run}/exports/${fmt}`;};
(async()=>{state.settings=await api('/settings');syncV9Controls();I.bindMessage($('mode'),state.settings.mode);I.bindText($('version'),()=>'v'+state.settings.version);await loadModelSettings();
 I.bindText($('mode-notice'),()=>state.settings.provider==='mock'?I.t('notice.mock'):
  (state.settings.live_ready?I.t('notice.live'):I.t('notice.blocked',{reasons:state.settings.live_blockers.map(I.message).join('; ')})));
 $('vision-disclosure').hidden=!state.settings.capabilities?.vision?.ready;
 if(state.settings.storage_warning){I.bindMessage($('environment-warning'),state.settings.storage_warning);$('environment-warning').hidden=false;}
 await loadConnectorStatus();renderConnectorItems();await projects();setInterval(()=>{if(state.run)refreshRun().catch(()=>{});},2500);
 setInterval(()=>{if(state.project&&state.referenceKnowledge?.active_update)refreshReferenceWorkspace().catch(()=>{});},2500);
 setInterval(()=>{if(state.project&&state.referenceEvaluationJobs.some(activeReferenceEvaluationJob))loadReferenceEvaluations().catch(()=>{});},2500);
 setInterval(reloadForServiceChange,4000);window.addEventListener('focus',reloadForServiceChange);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)reloadForServiceChange();});
})().catch(e=>toast(e.message));
