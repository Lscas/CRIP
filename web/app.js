'use strict';
const $ = id => document.getElementById(id);
const I = window.CIRPI18n;
I.init();
document.querySelectorAll('[data-i18n-initial]').forEach(node=>I.bindText(node,node.dataset.i18nInitial));
const state = {project:null,run:null,records:[],kind:'MATERIAL',settings:null,uploading:false};
const labels = {MATERIAL:'kind.MATERIAL',INSPECTION:'kind.INSPECTION',CONFLICT:'kind.CONFLICT',MISSING:'kind.MISSING'};
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function elT(tag,key,params={},cls){return I.bindText(el(tag,null,cls),key,params);}
function optionT(key,value=''){return I.bindText(new Option('',value),key);}
function toast(text){I.bindMessage($('toast'),text);$('toast').hidden=false;setTimeout(()=>{$('toast').hidden=true;},6500);}
async function api(path,method='GET',body){
 const headers={'X-CIRP-Client':'browser'};
 if(body!==undefined)headers['Content-Type']='application/json';
 const r=await fetch('/api'+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
 if(!r.ok){let err;try{err=await r.json();}catch{err={detail:'请求失败 '+r.status};}throw new Error(typeof err.detail==='string'?err.detail:JSON.stringify(err.detail));}
 return r.json();
}
function error(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};}
async function projects(selected){
 const all=await api('/projects');$('project-select').replaceChildren(optionT('project.select'));
 all.forEach(p=>$('project-select').append(new Option(p.name,p.id)));
 if(selected||all.length){$('project-select').value=selected||all[0].id;await selectProject($('project-select').value);}
}
async function selectProject(id){state.project=id;state.run=null;state.records=[];
 if(!id){I.bindText($('project-name'),'project.none');renderRecords();return;}
 const p=await api('/projects/'+id);I.bindText($('project-name'),()=>p.name);
 await refresh();
}
async function refresh(){
 if(!state.project)return;
 const manifest=await api(`/projects/${state.project}/manifest`);$('files-body').replaceChildren();
 manifest.uploads.forEach(u=>{const tr=el('tr');tr.append(el('td',u.name),el('td',(u.size/1024/1024).toFixed(2)+' MB'),I.bindStatus(el('td'),u.state),elT('td',u.state==='DUPLICATE'?'files.duplicate':u.state==='COMPLETE'?'files.hash':'files.chunk',{offset:u.offset,size:u.size}));$('files-body').append(tr);});
 I.bindText($('file-total'),'files.total',{count:manifest.documents.length});
 const runs=await api(`/projects/${state.project}/analysis-runs`);$('run-select').replaceChildren(optionT('run.select'));
 runs.forEach(r=>$('run-select').append(I.bindText(new Option('',r.id),()=>r.created_at.slice(0,19).replace('T',' ')+' · '+I.status(r.status))));
 if(!state.run&&runs.length)state.run=runs[0].id;
 if(state.run){$('run-select').value=state.run;await refreshRun();}else{renderRecords();I.bindText($('run-state'),'run.notStarted');}
 $('start').disabled=state.uploading||!manifest.documents.length;
}
async function refreshRun(){
 if(!state.run)return;
 const rid=state.run;const run=await api('/analysis-runs/'+rid);
 if(rid!==state.run)return;
 const cost=await api(`/analysis-runs/${rid}/cost`);state.records=await api(`/analysis-runs/${rid}/records`);
 I.bindStatus($('run-state'),run.status);I.bindText($('run-message'),()=>I.t('run.message',{stage:I.message(run.stage),message:I.message(run.message)}));
 const c=run.coverage;const done=(c.fragments_extracted||0)+(c.fragments_need_review||0);
 const ratio=c.fragments_total?Math.round(done/c.fragments_total*100):0;
 $('progress-bar').style.width=ratio+'%';
 I.bindText($('coverage'),'run.coverage',{processed:c.files_processed||0,total:c.files_total||0,done,fragments:c.fragments_total||0,review:c.fragments_need_review||0});
 I.bindText($('cost'),'run.cost',{spent:Number(cost.spent_cny).toFixed(4),reserved:Number(cost.reserved_cny).toFixed(4)});
 I.bindText($('coverage-detail'),()=>JSON.stringify({coverage:c,cost,capabilities:run.capabilities},null,2));
 const active=['QUEUED','RUNNING'].includes(run.status);$('pause').disabled=!active;
 $('resume').disabled=!['PAUSED','PAUSED_PROVIDER','PAUSED_BUDGET','INTERRUPTED'].includes(run.status);
 $('export-json').disabled=active;$('export-xlsx').disabled=active;renderRecords();
}
function sourceIds(c){const ids=new Set(c.evidence_ids||c.related_evidence_ids||[]);(c.claims||[]).forEach(x=>ids.add(x.evidence_id));return [...ids];}
function renderRecords(){
 Object.keys(labels).forEach(k=>{$('count-'+k).textContent=state.records.filter(x=>x.record.kind===k).length;});
 document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.kind===state.kind));
 const query=$('filter').value.toLowerCase();const rows=state.records.filter(x=>x.record.kind===state.kind&&JSON.stringify(x.record.candidate).toLowerCase().includes(query));
 $('results-body').replaceChildren();$('empty').hidden=rows.length>0;
 rows.forEach(row=>{const r=row.record,c=r.candidate;const tr=el('tr',null,'click-row');
  const name=el('td');const btn=el('button',c.name||c.requirement||c.subject,'link');btn.onclick=()=>showRecord(row);name.append(btn);
  const detail=c.design_properties?.map(x=>x.name+': '+x.value).join(' · ')||c.reason||c.blocking_reason||c.activity||'';
  name.append(el('small',detail));
  const status=el('td');status.append(I.bindStatus(el('span',null,'badge'),c.requirement_status||c.resolution_status||c.reason_code));status.append(elT('small','verify.status.'+(row.verification?.status||'NOT_CHECKED'),{},'verification-summary'));
  const review=el('td');review.append(I.bindStatus(el('span',null,'badge '+(r.review.status==='PENDING'?'warn':'')),r.review.status));
  const evidence=el('td');sourceIds(c).forEach(eid=>{const b=el('button',eid.slice(0,14)+'…','link evidence-button');b.onclick=error(()=>showEvidence(eid));evidence.append(b);});
  tr.append(name,status,review,evidence);$('results-body').append(tr);
 });
}
function showRecord(row){
 const r=row.record,c=r.candidate;$('drawer').hidden=false;I.bindText($('drawer-title'),labels[r.kind]);const body=$('drawer-body');body.replaceChildren();
 body.append(el('p',c.name||c.requirement||c.subject));
 body.append(elT('p','record.note',{},'muted'));body.append(verificationPanel(row));
 const area=el('textarea');area.value=JSON.stringify(c,null,2);area.setAttribute('data-i18n-aria-label','record.json');I.applyElement(area);body.append(area);
 const note=el('input');note.setAttribute('data-i18n-placeholder','record.reviewNote');note.setAttribute('data-i18n-aria-label','record.reviewNoteLabel');I.applyElement(note);body.append(note);
 const actions=el('div',null,'row');
 ['ACCEPTED','EDITED','REJECTED'].forEach((action,i)=>{const b=elT('button',['record.accept','record.edit','record.reject'][i],{},i===0?'primary':'outline');b.onclick=error(async()=>{
  await api(`/records/${r.meta.record_id}/review`,'POST',{action,expected_version:row.review_version,note:note.value,...(action==='EDITED'?{candidate:JSON.parse(area.value)}:{})});
  $('drawer').hidden=true;await refreshRun();toast('审核记录已保存');});actions.append(b);});body.append(actions,el('hr'));
 sourceIds(c).forEach(id=>{const b=elT('button','record.source',{id},'link evidence-button');b.onclick=error(()=>showEvidence(id));body.append(b);});
 const history=elT('button','record.history',{},'outline');history.onclick=error(async()=>{const data=await api(`/records/${r.meta.record_id}/history`);body.append(el('pre',JSON.stringify(data,null,2)));});body.append(history);
}
async function showEvidence(id,range=null,runId=state.run){
 const data=await api(`/analysis-runs/${runId}/evidence/${encodeURIComponent(id)}`);const e=data.evidence;
 $('drawer').hidden=false;I.bindText($('drawer-title'),'evidence.title');const body=$('drawer-body');body.replaceChildren();
 body.append(el('h3',data.file_name),I.bindText(el('p',null,'muted'),()=>I.t('evidence.revision',{date:e.internal_revision_date||I.t('evidence.unknownDate')})));
 body.append(el('pre',JSON.stringify(e.locator,null,2)));
 const original=el('pre');
 if(range&&Number.isInteger(range.start)&&Number.isInteger(range.end)&&range.start>=0&&range.end<=Array.from(e.raw_text).length){
  const chars=Array.from(e.raw_text);original.append(document.createTextNode(chars.slice(0,range.start).join('')),el('mark',chars.slice(range.start,range.end).join('')),document.createTextNode(chars.slice(range.end).join('')));
 }else original.textContent=e.raw_text;body.append(original);
 const a=elT('a','evidence.open',{},'button outline');a.href='/api/documents/'+e.document_id+'/file';a.target='_blank';a.rel='noopener';body.append(a);
 body.append(elT('p','evidence.note',{},'muted'));
}
function verificationPanel(row){
 const root=el('section',null,'verification-panel');root.dataset.verificationPanel='true';
 const report=row.verification||{status:'NOT_CHECKED',fields:[]};
 root.append(elT('h3','verify.title'),elT('span','verify.status.'+report.status,{},'badge warn'),elT('p','verify.disclaimer',{},'muted'));
 const toolbar=el('div',null,'row');
 const check=elT('button','verify.check',{},'outline');
 check.onclick=error(async()=>{await api(`/records/${row.record.meta.record_id}/verification`,'POST',{expected_version:row.review_version,semantic:false});await refreshRun();const fresh=state.records.find(x=>x.record.meta.record_id===row.record.meta.record_id);if(fresh)showRecord(fresh);});
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
   card.append(b,elT('small','verify.role.'+q.role),el('blockquote',q.quote));
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
 if(!state.project){toast('先创建或选择项目');return;}
 if(state.uploading){toast('已有上传正在进行');return;}
 state.uploading=true;$('start').disabled=true;
 try{for(const file of files){
  const key=['cirp-upload',state.project,file.webkitRelativePath||file.name,file.size,file.lastModified].join(':');
  let u;const saved=localStorage.getItem(key);
  if(saved){try{u=await api('/uploads/'+saved);}catch{localStorage.removeItem(key);}}
  if(!u||u.state==='ABORTED'){u=await api(`/projects/${state.project}/uploads`,'POST',{name:file.name,size:file.size});localStorage.setItem(key,u.id);}
  if(!['COMPLETE','DUPLICATE'].includes(u.state)){
   // 校验已接收分片，避免同名文件被替换后产生混合内容。
   for(const chunk of u.chunks||[]){
    const digest=await crypto.subtle.digest('SHA-256',await file.slice(chunk.offset,chunk.offset+chunk.size).arrayBuffer());
    const hex=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hex!==chunk.checksum){localStorage.removeItem(key);await api(`/uploads/${u.id}/abort`,'POST');throw new Error('续传文件内容已变化，请重新选择文件上传。');}
   }
   for(let offset=u.offset;offset<file.size;){
    const part=file.slice(offset,offset+state.settings.upload_chunk_bytes);
    const r=await fetch(`/api/uploads/${u.id}/chunk?offset=${offset}`,{method:'PUT',headers:{'X-CIRP-Client':'browser','Content-Type':'application/octet-stream'},body:part});
    if(!r.ok){let e=await r.json();throw new Error(e.detail||'上传失败；重新选择原文件可续传。');}
    const x=await r.json();offset=x.offset;const progress=file.name+' · '+Math.round(offset/file.size*100)+'%';I.bindText($('upload-progress'),()=>progress);
   }
   await api(`/uploads/${u.id}/complete`,'POST');
  }
  localStorage.removeItem(key);await refresh();
 }I.bindText($('upload-progress'),'upload.complete');}finally{state.uploading=false;await refresh();}
}
$('new-project').onclick=()=>{$('project-dialog').showModal();$('project-input').focus();};
$('close-project').onclick=()=>{$('project-dialog').close();};
$('project-form').onsubmit=error(async e=>{e.preventDefault();const p=await api('/projects','POST',{name:$('project-input').value});$('project-dialog').close();$('project-input').value='';await projects(p.id);});
$('project-select').onchange=error(()=>selectProject($('project-select').value));
$('run-select').onchange=error(async()=>{state.run=$('run-select').value;await refreshRun();});
$('start').onclick=error(async()=>{if(!state.project)return;const run=await api(`/projects/${state.project}/analysis-runs`,'POST');state.run=run.id;await refresh();});
$('pause').onclick=error(async()=>{await api(`/analysis-runs/${state.run}/pause`,'POST');await refreshRun();});
$('resume').onclick=error(async()=>{await api(`/analysis-runs/${state.run}/resume`,'POST');await refreshRun();});
$('file-input').onchange=error(async e=>{await uploadFiles([...e.target.files]);e.target.value='';});
$('folder-input').onchange=error(async e=>{await uploadFiles([...e.target.files]);e.target.value='';});
$('drop-zone').ondragover=e=>{e.preventDefault();$('drop-zone').classList.add('drag');};
$('drop-zone').ondragleave=()=>{$('drop-zone').classList.remove('drag');};
$('drop-zone').ondrop=error(async e=>{e.preventDefault();$('drop-zone').classList.remove('drag');await uploadFiles([...e.dataTransfer.files]);});
$('close-drawer').onclick=()=>{$('drawer').hidden=true;};
$('filter').oninput=renderRecords;
for(const b of document.querySelectorAll('[data-kind]'))b.onclick=()=>{state.kind=b.dataset.kind;renderRecords();$('results').scrollIntoView({behavior:'smooth'});};
for(const fmt of ['json','xlsx'])$('export-'+fmt).onclick=()=>{if(state.run)location.href=`/api/analysis-runs/${state.run}/exports/${fmt}`;};
(async()=>{state.settings=await api('/settings');I.bindMessage($('mode'),state.settings.mode);I.bindText($('version'),()=>'v'+state.settings.version);
 I.bindText($('mode-notice'),()=>state.settings.provider==='mock'?I.t('notice.mock'):
  (state.settings.live_ready?I.t('notice.live'):I.t('notice.blocked',{reasons:state.settings.live_blockers.map(I.message).join(I.language==='en'?'; ':'；')})));
 if(state.settings.storage_warning){I.bindMessage($('environment-warning'),state.settings.storage_warning);$('environment-warning').hidden=false;}
 await projects();setInterval(()=>{if(state.run)refreshRun().catch(()=>{});},2500);
})().catch(e=>toast(e.message));
