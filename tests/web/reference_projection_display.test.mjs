/* Execute the production renderer against a small offline DOM surrogate.
 * This is a display-boundary test, not full-browser or phone acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const app=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const i18n=fs.readFileSync(new URL('../../web/i18n.js',import.meta.url),'utf8');
const catalog=JSON.parse(i18n.match(/const catalog = ([\s\S]*?);\r?\n  const statusCatalog/)[1]);
const start=app.indexOf('function projectionReviewAvailable(){');
const end=app.indexOf('function renderComparisonSide(',start);
assert.ok(start>=0&&end>start);
const renderer=app.slice(start,end);

const base={result_id:'rr-1',result_hash:'a'.repeat(64),run_id:'run-1',snapshot_id:'snapshot-1',question:'Synthetic question?',provider:'mock',model:'mock',created_at:'2026-10-06T00:00:00Z',status:'ANSWERED',review:{status:'PENDING',version:0},result:{answer:'DO NOT RENDER UNSUPPORTED ANSWER',execution_receipts:[]},citations:[{quote:'unsafe quote'}]};
const geometry={coordinate_system:'pdf-cropbox-points-top-left',page_width:100,page_height:100,rotation:0,bbox:[1,1,10,10],render_crop_bbox:[1,1,10,10],render_mode:'SOURCE_CROP'};
const review={view_version:'reference-projection-review-view-1',result_id:base.result_id,result_hash:base.result_hash,result_kind:'PROJECTION_LOOP_OUTCOME',run_id:base.run_id,snapshot_id:base.snapshot_id,question:base.question,projection_execution_version:'reference-projection-execution-2',loop_version:'project-projection-loop-2',status:'REVIEW_REQUIRED',object_condition_relations_verified:false,answer_completeness_verified:false,selections:[{ordinal:1,document_name:'safe.pdf',text:'<unsafe & full original text>',page_number:2,part_refs:['P1'],geometry}]};

function makeNode(tag,text=null,cls=''){
 return {tag,text,cls,children:[],attrs:{},dataset:{},disabled:false,removed:false,isConnected:true,
  append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},
  setAttribute(key,value){this.attrs[key]=value;},remove(){this.removed=true;this.isConnected=false;},replaceWith(node){this.replacedWith=node;},
 };
}
function flatten(entry){return [entry,...entry.children.flatMap(flatten),...(entry.replacedWith?[...flatten(entry.replacedWith)]:[])];}
function createHarness({language='en',item=base,capability=false,response=review,project='project-1',legacy=false}={}){
 const root=makeNode('main');const calls=[],all=[root];
 const translate=(key,args={})=>{assert.ok(Object.hasOwn(catalog[language],key),`missing ${language} ${key}`);return catalog[language][key].replace(/\{(\w+)\}/g,(_match,name)=>String(args[name]??''));};
 const legacyOnly=(name,value)=>()=>{assert.ok(legacy,`unsupported record reached ${name}`);return value;};
 const create=(tag,text=null,cls='')=>{const node=makeNode(tag,text,cls);all.push(node);return node;};
 const context=vm.createContext({state:{project,settings:capability?{capabilities:{reference_projection_review:{available:true,view_version:'reference-projection-review-view-1'}}}:{},referenceResults:[item]},$:()=>root,el:create,
  elT:(tag,key,args={},cls='')=>create(tag,translate(key,args),cls),
  I:{bindStatus:(element,value)=>{element.text=value;return element;},applyElement(){},bindText:(element,key,args={})=>{element.text=translate(key,args);return element;},t:translate},
  error:fn=>async(...args)=>{try{return await fn(...args);}catch(error){throw error;}},referenceCitation:legacyOnly('citations',makeNode('blockquote','legacy citation')),
  referenceExecutionReceipts:legacyOnly('receipts',null),referenceCaseButton:legacyOnly('case creation',makeNode('button','legacy follow-up')),
  api:async path=>{calls.push(path);return typeof response==='function'?await response():response;},encodeURIComponent});
 vm.runInContext(renderer+'\nrenderReferenceResults();',context);
 return {context,root,calls,all,nodes:()=>flatten(root)};
}
function button(harness,key){return harness.nodes().find(node=>node.tag==='button'&&node.text===catalog.en[key]);}

for(const language of ['zh-CN','en']){
 for(const status of ['REVIEW_REQUIRED','CANNOT_ANSWER','ANSWERED']){
  test(`${language}: projection ${status} stays out of legacy renderer`,()=>{
   const h=createHarness({language,item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status},capability:false});
   assert.ok(h.nodes().some(node=>node.text===catalog[language]['reference.nonAnswerRecord']));
   assert.ok(h.nodes().some(node=>node.text===catalog[language]['reference.resultWorkflowUnavailable']));
   assert.ok(!h.nodes().some(node=>node.cls==='reference-answer'||node.text===base.result.answer));
  });
 }
 test(`${language}: unknown explicit kind fails closed`,()=>{
  const h=createHarness({language,item:{...base,result_kind:'UNKNOWN_FUTURE_KIND'}});
  assert.ok(h.nodes().some(node=>node.text===catalog[language]['reference.resultWorkflowUnavailable']));
  assert.ok(!h.nodes().some(node=>node.tag==='input'||node.tag==='blockquote'));
 });
}
test('capability is required and only REVIEW_REQUIRED projection receives the source button',()=>{
 const allowed=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true});
 assert.ok(button(allowed,'reference.projectionSourceOpen'));
 const closed=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'CANNOT_ANSWER'},capability:true});
 assert.equal(button(closed,'reference.projectionSourceOpen'),undefined);
});
test('capability and returned contract versions plus nonempty ordered sources are required',async()=>{
 const wrongCapability=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true});
 wrongCapability.context.state.settings.capabilities.reference_projection_review={available:true,view_version:'future-view'};
 wrongCapability.context.renderReferenceResults();
 assert.equal(button(wrongCapability,'reference.projectionSourceOpen'),undefined);
 for(const response of [{...review,view_version:'future-view'},{...review,projection_execution_version:'future-execution'},{...review,loop_version:'future-loop'},{...review,selections:[]},{...review,selections:[{...review.selections[0],ordinal:2}]}]){
  const h=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response});
  await button(h,'reference.projectionSourceOpen').onclick();assert.equal(h.nodes().some(node=>node.cls==='projection-review-view'),false);
 }
});
test('source button calls only review endpoint, renders full safe text, and defers image loading',async()=>{
 const longText=`<tag>${'x'.repeat(9000)}&`;
 const h=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:{...review,selections:[{...review.selections[0],text:longText}]}});
 const open=button(h,'reference.projectionSourceOpen');await open.onclick();
 assert.deepEqual(h.calls,[`/reference-results/${base.result_id}/projection-review`]);
 const text=h.nodes().find(node=>node.cls==='projection-review-text');assert.equal(text.text,longText);
 assert.equal(h.nodes().filter(node=>node.tag==='img').length,0);
 const image=button(h,'reference.projectionSourceImage');image.onclick();
 const preview=h.all.find(node=>node.tag==='img');assert.equal(preview.src,`/api/reference-results/${base.result_id}/projection-review/sources/1/image`);
 assert.equal(h.calls.length,1);
});
test('tampered review identity and stale project response are discarded without sources or legacy content',async()=>{
 const tampered=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:{...review,result_hash:'b'.repeat(64)}});
 await button(tampered,'reference.projectionSourceOpen').onclick();
 assert.equal(tampered.nodes().some(node=>node.cls==='projection-review-view'),false);
 const stale=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:async()=>{stale.context.state.project='different';return review;}});
 await button(stale,'reference.projectionSourceOpen').onclick();
 assert.equal(stale.nodes().some(node=>node.cls==='projection-review-view'),false);
 assert.ok(!stale.nodes().some(node=>node.cls==='reference-answer'||node.tag==='blockquote'));
 const replaced=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:async()=>{replaced.context.state.referenceResults=[{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED',result_hash:'c'.repeat(64)}];return review;}});
 await button(replaced,'reference.projectionSourceOpen').onclick();
 assert.equal(replaced.nodes().some(node=>node.cls==='projection-review-view'),false);
});
test('malformed selection or request failure shows no source text or image',async()=>{
 const malformed=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:{...review,selections:[{...review.selections[0],text:42}]}});
 await button(malformed,'reference.projectionSourceOpen').onclick();
 assert.equal(malformed.nodes().some(node=>node.cls==='projection-review-view'),false);
 assert.equal(malformed.nodes().some(node=>node.tag==='img'),false);
 const failed=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true,response:()=>{throw new Error('offline');}});
 await button(failed,'reference.projectionSourceOpen').onclick();
 assert.ok(failed.nodes().some(node=>node.text===catalog.en['reference.projectionSourceUnavailable']));
});
test('late image events cannot mutate a detached card or replaced result identity',async()=>{
 const h=createHarness({item:{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED'},capability:true});
 await button(h,'reference.projectionSourceOpen').onclick();
 const image=button(h,'reference.projectionSourceImage');image.onclick();
 const preview=h.all.find(node=>node.tag==='img');
 const card=h.nodes().find(node=>node.cls==='reference-result-card');card.isConnected=false;preview.onload();
 assert.equal(image.replacedWith,undefined);
 card.isConnected=true;h.context.state.referenceResults=[{...base,result_kind:'PROJECTION_LOOP_OUTCOME',status:'REVIEW_REQUIRED',result_hash:'d'.repeat(64)}];preview.onerror();
 assert.notEqual(image.text,catalog.en['reference.projectionSourceImageUnavailable']);
});
test('legacy result retains answer, citations and both review actions',()=>{
 const h=createHarness({item:base,legacy:true});
 assert.ok(h.nodes().some(node=>node.cls==='reference-answer'&&node.text===base.result.answer));
 assert.ok(h.nodes().some(node=>node.tag==='blockquote'));
 for(const key of ['reference.accept','reference.reject'])assert.ok(h.nodes().some(node=>node.tag==='button'&&node.text===catalog.en[key]));
});
