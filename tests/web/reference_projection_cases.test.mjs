/* Exercise the production case renderer with an offline DOM surrogate. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const app=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const i18n=fs.readFileSync(new URL('../../web/i18n.js',import.meta.url),'utf8');
const catalog=JSON.parse(i18n.match(/const catalog = ([\s\S]*?);\r?\n  const statusCatalog/)[1]);
const start=app.indexOf('function caseAttachmentPicker('),end=app.indexOf('function renderComparisonSide(',start);
assert.ok(start>=0&&end>start);
const renderer=app.slice(start,end);
const hash='a'.repeat(64);
const geometry={coordinate_system:'pdf-cropbox-points-top-left',page_width:100,page_height:100,rotation:0,bbox:[1,1,10,10],render_crop_bbox:[1,1,10,10],render_mode:'SOURCE_CROP'};
const review={view_version:'reference-projection-review-view-1',projection_execution_version:'reference-projection-execution-2',loop_version:'project-projection-loop-2',result_id:'result-1',result_hash:hash,run_id:'run-1',snapshot_id:'snapshot-1',question:'Synthetic projection question?',result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED',object_condition_relations_verified:false,answer_completeness_verified:false,selections:[{ordinal:1,document_name:'safe.pdf',text:'safe source',page_number:1,part_refs:['P1'],geometry}]};
const source={status:'REVIEW_REQUIRED',result_id:'result-1',result_kind:'PROJECTION_LOOP_OUTCOME',result_hash:hash};
const caseItem={case_id:'case-1',case_view_version:'reference-case-view-2',project_id:'project-1',run_id:'run-1',snapshot_id:'snapshot-1',question:review.question,source,status:'OPEN',version:7,attachments:[],followups:[],history:[]};

function node(tag,text=null,cls=''){return {tag,text,cls,children:[],attrs:{},dataset:{},isConnected:true,disabled:false,value:'',files:[],selectedOptions:[],append(...xs){this.children.push(...xs);this.selectedOptions=this.children.filter(x=>x?.selected);},replaceChildren(...xs){this.children=[];this.append(...xs);},setAttribute(k,v){this.attrs[k]=v;},remove(){this.isConnected=false;this.removed=true;},replaceWith(value){this.replacedWith=value;}};}
function all(root){if(!root||typeof root!=='object')return [];return [root,...(root.children||[]).flatMap(all),...(root.replacedWith?all(root.replacedWith):[])];}
function harness({response=review,results=[],caseData=caseItem}={}){
 const roots={'reference-case-detail':node('main'),'reference-cases':node('main'),'reference-results':node('main')};const calls=[];
 const t=(key,args={})=>{assert.ok(Object.hasOwn(catalog.en,key),`missing ${key}`);return catalog.en[key].replace(/\{(\w+)\}/g,(_m,k)=>String(args[k]??''));};
 const capability={available:true,view_version:'reference-projection-review-view-1',case_workflow_available:true,case_view_version:'reference-case-view-2',saved_followup_link_available:true,question_creation_available:false};
 const context=vm.createContext({state:{project:'project-1',settings:{capabilities:{reference_projection_review:capability}},referenceCaseSelected:structuredClone(caseData),referenceResults:results,referenceRuns:[],documents:[],caseFlowByCase:{},caseFlowBusy:false,uploading:false,unresolvedCalls:[],referenceKnowledge:null,runStatus:null,referenceAsking:false,referencePreviewing:false,referenceEvaluationBusy:false,asking:false,referenceEvaluationJobs:[]},$:id=>roots[id]||node('div'),el:node,elT:(tag,key,args={},cls='')=>node(tag,t(key,args),cls),optionT:key=>new context.Option(t(key),''),Option:function(text,value){return {tag:'option',text,value,selected:false};},I:{t, status:value=>value,bindText:(n,key,args={})=>{n.text=t(key,args);return n;},bindStatus:(n,value)=>{n.text=value;return n;},applyElement(){}},v9Enabled:()=>false,error:fn=>async(...args)=>fn(...args),updateReferenceAvailability(){},loadReferenceCases:async()=>{},loadReferenceResults:async()=>{},recoverPendingReferenceResult:async()=>{},renderReferencePreview(){},uploadFiles:async()=>[],toast(){},referenceCitation:()=>node('blockquote','legacy'),api:async(path,method='GET')=>{calls.push({path,method});if(typeof response==='function')return response();if(response instanceof Error)throw response;return response;},encodeURIComponent});
 vm.runInContext(renderer+'\nrenderReferenceCaseDetail();',context);return {context,roots,calls,nodes:()=>all(roots['reference-case-detail'])};
}
const find=(h,predicate)=>h.nodes().find(predicate);

test('projection case retains the human form when its source is unavailable',async()=>{
 const h=harness({response:new Error('409 missing PDF')});
 const sourceButton=find(h,n=>n.tag==='button'&&n.text===catalog.en['reference.caseViewSource']);
 assert.ok(find(h,n=>n.tag==='form'&&n.cls.includes('reference-case-form')));
 await sourceButton.onclick();
 assert.deepEqual(h.calls,[{path:'/reference-results/result-1/projection-review',method:'GET'}]);
 assert.ok(find(h,n=>n.tag==='form'&&n.cls.includes('reference-case-form')));
 assert.ok(find(h,n=>n.text===catalog.en['reference.projectionSourceUnavailable']));
 assert.equal(h.calls.some(call=>call.method==='POST'),false);
});

test('unknown case source kind fails closed without removing the human form',()=>{
 const unknown={...caseItem,source:{status:'ANSWERED',result_id:'unknown-source',result_kind:'UNKNOWN_FUTURE_KIND',result_hash:hash}};
 const h=harness({caseData:unknown});
 assert.ok(find(h,n=>n.tag==='form'&&n.cls.includes('reference-case-form')));
 assert.ok(find(h,n=>n.text===catalog.en['reference.caseSourceWorkflowUnavailable']));
 assert.equal(find(h,n=>n.tag==='button'&&n.text===catalog.en['reference.caseViewSource']),undefined);
 assert.equal(find(h,n=>n.tag==='button'&&n.text===catalog.en['reference.caseFlowAsk']),undefined);
 assert.deepEqual(h.calls,[]);
});

test('case source response is discarded when case identity changes in flight',async()=>{
 for(const change of [item=>item.version=8,item=>item.source.result_hash='b'.repeat(64),item=>item.case_view_version='future-view',item=>item.case_id='case-2',(_item,h)=>h.context.state.project='other-project']){
  let h;h=harness({response:async()=>{change(h.context.state.referenceCaseSelected,h);return review;}});
  await find(h,n=>n.tag==='button'&&n.text===catalog.en['reference.caseViewSource']).onclick();
  assert.equal(find(h,n=>n.cls==='projection-review-view'),undefined);
 }
});

test('projection follow-up picker keeps same-project same-question new terminal projection results only',()=>{
 const good=['REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'].map((status,index)=>({project_id:'project-1',run_id:`new-${index}`,result_id:`good-${index}`,question:review.question,result_kind:'PROJECTION_LOOP_OUTCOME',status}));
 const h=harness({results:[...good,{...good[0],result_id:'same-run',run_id:'run-1'},{...good[0],result_id:'wrong-project',project_id:'other'},{...good[0],result_id:'wrong-question',question:'other'},{...good[0],result_id:'wrong-projection-status',status:'ANSWERED'},{...good[0],result_id:'unknown-kind',result_kind:'UNKNOWN_FUTURE_KIND',status:'ANSWERED'}]});
 const picker=find(h,n=>n.tag==='select'&&n.children.some(option=>option.value==='good-0'));
 assert.deepEqual(picker.children.map(option=>option.value),['good-0','good-1','good-2']);
 assert.ok(find(h,n=>n.text===catalog.en['reference.caseProjectionLegacyQuestion']));
});

test('saved follow-up picker permits mixed legacy and projection result kinds under the capability',()=>{
 const legacy={...caseItem,source:{status:'ANSWERED',result_id:'legacy-source',result_kind:'REFERENCE_QA_RESULT',result_hash:hash}};
 let h=harness({caseData:legacy,results:[{project_id:'project-1',run_id:'new-projection',result_id:'projection',question:review.question,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'}]});
 assert.ok(find(h,n=>n.tag==='select'&&n.children.some(option=>option.value==='projection')));
 h=harness({results:[{project_id:'project-1',run_id:'new-legacy',result_id:'legacy',question:review.question,status:'ANSWERED'}]});
 assert.ok(find(h,n=>n.tag==='select'&&n.children.some(option=>option.value==='legacy')));
});

test('full capability exposes a guarded human-case action for each supported projection terminal state',()=>{
 const results=['REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'].map((status,index)=>({project_id:'project-1',result_id:`result-${index}`,result_hash:hash,run_id:`run-${index}`,snapshot_id:'snapshot-1',question:review.question,result_kind:'PROJECTION_LOOP_OUTCOME',status,provider:'mock',model:'mock',created_at:'2026-10-07T00:00:00Z'}));
 const h=harness({results});h.context.renderReferenceResults();
 const buttons=all(h.roots['reference-results']).filter(n=>n.tag==='button'&&n.text===catalog.en['reference.projectionSendToHuman']);
 assert.equal(buttons.length,3);
});
