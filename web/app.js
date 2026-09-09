'use strict';
const $ = id => document.getElementById(id);
const state = {project:null,run:null,records:[],kind:'MATERIAL',settings:null,uploading:false};
const labels = {MATERIAL:'材料',INSPECTION:'检查 / 测试 / 报告',CONFLICT:'冲突 / 版本差异',MISSING:'缺失 / 未处理'};
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;setTimeout(()=>{$('toast').hidden=true;},6500);}
async function api(path,method='GET',body){
 const headers={'X-CIRP-Client':'browser'};
 if(body!==undefined)headers['Content-Type']='application/json';
 const r=await fetch('/api'+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
 if(!r.ok){let err;try{err=await r.json();}catch{err={detail:'请求失败 '+r.status};}throw new Error(typeof err.detail==='string'?err.detail:JSON.stringify(err.detail));}
 return r.json();
}
function error(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};}
async function projects(selected){
 const all=await api('/projects');$('project-select').replaceChildren(new Option('选择项目',''));
 all.forEach(p=>$('project-select').append(new Option(p.name,p.id)));
 if(selected||all.length){$('project-select').value=selected||all[0].id;await selectProject($('project-select').value);}
}
async function selectProject(id){state.project=id;state.run=null;state.records=[];
 if(!id){$('project-name').textContent='尚未选择项目';renderRecords();return;}
 const p=await api('/projects/'+id);$('project-name').textContent=p.name;
 await refresh();
}
async function refresh(){
 if(!state.project)return;
 const manifest=await api(`/projects/${state.project}/manifest`);$('files-body').replaceChildren();
 manifest.uploads.forEach(u=>{const tr=el('tr');tr.append(el('td',u.name),el('td',(u.size/1024/1024).toFixed(2)+' MB'),el('td',u.state),el('td',u.state==='DUPLICATE'?'相同内容，复用来源':u.state==='COMPLETE'?'SHA-256 已记录':'分片 '+u.offset+' / '+u.size));$('files-body').append(tr);});
 $('file-total').textContent=manifest.documents.length+' 个不同内容文件';
 const runs=await api(`/projects/${state.project}/analysis-runs`);$('run-select').replaceChildren(new Option('选择分析运行',''));
 runs.forEach(r=>$('run-select').append(new Option(r.created_at.slice(0,19).replace('T',' ')+' · '+r.status,r.id)));
 if(!state.run&&runs.length)state.run=runs[0].id;
 if(state.run){$('run-select').value=state.run;await refreshRun();}else{renderRecords();$('run-state').textContent='未开始';}
 $('start').disabled=state.uploading||!manifest.documents.length;
}
async function refreshRun(){
 if(!state.run)return;
 const rid=state.run;const run=await api('/analysis-runs/'+rid);
 if(rid!==state.run)return;
 const cost=await api(`/analysis-runs/${rid}/cost`);state.records=await api(`/analysis-runs/${rid}/records`);
 $('run-state').textContent=run.status;$('run-message').textContent=run.stage+'：'+run.message;
 const c=run.coverage;const done=(c.fragments_extracted||0)+(c.fragments_need_review||0);
 const ratio=c.fragments_total?Math.round(done/c.fragments_total*100):0;
 $('progress-bar').style.width=ratio+'%';
 $('coverage').textContent=`文件 ${c.files_processed||0}/${c.files_total||0} · 片段 ${done}/${c.fragments_total||0} · ${c.fragments_need_review||0} 待审核`;
 $('cost').textContent=`已记API费 ¥${Number(cost.spent_cny).toFixed(4)} · 预留 ¥${Number(cost.reserved_cny).toFixed(4)} / ¥300`;
 $('coverage-detail').textContent=JSON.stringify({coverage:c,cost,capabilities:run.capabilities},null,2);
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
  const status=el('td');status.append(el('span',c.requirement_status||c.resolution_status||c.reason_code,'badge'));
  const review=el('td');review.append(el('span',r.review.status,'badge '+(r.review.status==='PENDING'?'warn':'')));
  const evidence=el('td');sourceIds(c).forEach(eid=>{const b=el('button',eid.slice(0,14)+'…','link evidence-button');b.onclick=error(()=>showEvidence(eid));evidence.append(b);});
  tr.append(name,status,review,evidence);$('results-body').append(tr);
 });
}
function showRecord(row){
 const r=row.record,c=r.candidate;$('drawer').hidden=false;$('drawer-title').textContent=labels[r.kind];const body=$('drawer-body');body.replaceChildren();
 body.append(el('p',c.name||c.requirement||c.subject));
 body.append(el('p','全部候选须人工核验。编辑使用受校验的JSON，不允许修改证据身份或元数据。','muted'));
 const area=el('textarea');area.value=JSON.stringify(c,null,2);area.setAttribute('aria-label','候选JSON');body.append(area);
 const note=el('input');note.placeholder='修改/审核说明';note.setAttribute('aria-label','审核说明');body.append(note);
 const actions=el('div',null,'row');
 ['ACCEPTED','EDITED','REJECTED'].forEach((action,i)=>{const b=el('button',['接受','保存修改','拒绝'][i],i===0?'primary':'outline');b.onclick=error(async()=>{
  await api(`/records/${r.meta.record_id}/review`,'POST',{action,expected_version:row.review_version,note:note.value,...(action==='EDITED'?{candidate:JSON.parse(area.value)}:{})});
  $('drawer').hidden=true;await refreshRun();toast('审核记录已保存');});actions.append(b);});body.append(actions,el('hr'));
 sourceIds(c).forEach(id=>{const b=el('button','查看来源 '+id,'link evidence-button');b.onclick=error(()=>showEvidence(id));body.append(b);});
 const history=el('button','读取审核历史','outline');history.onclick=error(async()=>{const data=await api(`/records/${r.meta.record_id}/history`);body.append(el('pre',JSON.stringify(data,null,2)));});body.append(history);
}
async function showEvidence(id){
 const data=await api(`/analysis-runs/${state.run}/evidence/${encodeURIComponent(id)}`);const e=data.evidence;
 $('drawer').hidden=false;$('drawer-title').textContent='原始证据';const body=$('drawer-body');body.replaceChildren();
 body.append(el('h3',data.file_name),el('p','内部修订日期：'+(e.internal_revision_date||'未可靠识别'),'muted'));
 body.append(el('pre',JSON.stringify(e.locator,null,2)),el('pre',e.raw_text));
 const a=el('a','打开 / 下载原文件','button outline');a.href='/api/documents/'+e.document_id+'/file';a.target='_blank';a.rel='noopener';body.append(a);
 body.append(el('p','当前展示原文与定位坐标；PDF图面高亮查看器尚未接入。','muted'));
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
    const x=await r.json();offset=x.offset;$('upload-progress').textContent=file.name+' · '+Math.round(offset/file.size*100)+'%';
   }
   await api(`/uploads/${u.id}/complete`,'POST');
  }
  localStorage.removeItem(key);await refresh();
 }$('upload-progress').textContent='本批文件上传完成。';}finally{state.uploading=false;await refresh();}
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
(async()=>{state.settings=await api('/settings');$('mode').textContent=state.settings.mode;$('version').textContent='v'+state.settings.version;
 $('mode-notice').textContent=state.settings.provider==='mock'?'当前为零费用模拟模式，只识别明确的演示样例。真实文件只验证上传与解析；不会生成假材料来冒充模型分析。':
  (state.settings.live_ready?'真实API已启用；点击分析会产生费用。简单任务使用Flash非思考，所有结果须人工审核。':'真实API尚未就绪：'+state.settings.live_blockers.join('；'));
 await projects();setInterval(()=>{if(state.run)refreshRun().catch(()=>{});},2500);
})().catch(e=>toast(e.message));
