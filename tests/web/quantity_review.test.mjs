import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const renderer=source.slice(source.indexOf('async function showRecord('),source.indexOf('async function showEvidence('));
function node(tag,text=null){return {tag,text,children:[],disabled:false,hidden:false,
 append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(){}};}
function flatten(root){return [root,...root.children.flatMap(flatten)];}
async function harness(quantity={value:7,unit:'EA'},status='REJECTED'){
 const roots={'drawer':node('div'),'drawer-title':node('h2'),'drawer-body':node('section')},calls=[];
 const row={review_version:4,record:{kind:'MATERIAL',meta:{record_id:'synthetic'},review:{status:'ACCEPTED'},quantity_review:status,
  candidate:{candidate_key:'synthetic',name:'PANEL_A1',quantity,design_properties:[],evidence_ids:[]}}};
 const context=vm.createContext({$:id=>roots[id],el:node,elT:(tag,key,args={})=>node(tag,key+JSON.stringify(args)),
  labels:{MATERIAL:'Material'},I:{t:key=>key,status:value=>value,bindText:(n,t)=>n,applyElement(){}},
  verificationPanel:()=>node('div'),sourceIds:()=>[],error:fn=>fn,toast(){},refreshRun:async()=>{},
  api:async(path,method,payload)=>{calls.push({path,method,payload});return [];}});
 vm.runInContext(renderer,context);await context.showRecord(row);return {roots,calls,row,nodes:()=>flatten(roots['drawer-body'])};
}
for(const [action,label] of [['VERIFIED','record.quantityVerify'],['REJECTED','record.quantityReject'],['PENDING','record.quantityPending']]){
 test(`${action} quantity operation cannot implicitly accept the material`,async()=>{
  const h=await harness();assert.ok(h.nodes().some(n=>n.text?.includes('REJECTED')));
  const button=h.nodes().find(n=>n.tag==='button'&&n.text.startsWith(label));assert.ok(button);
  await button.onclick();assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].payload.quantity_action,action);assert.equal(h.calls[0].payload.expected_version,4);
  assert.equal(Object.hasOwn(h.calls[0].payload,'action'),false);
  assert.equal(Object.hasOwn(h.calls[0].payload,'candidate'),false);
 });
}
test('material acceptance does not submit an implicit quantity decision',async()=>{
 const h=await harness();await h.nodes().find(n=>n.tag==='button'&&n.text.startsWith('record.accept')).onclick();
 assert.equal(h.calls[0].payload.action,'ACCEPTED');assert.equal(Object.hasOwn(h.calls[0].payload,'quantity_action'),false);
});
test('no quantity controls are offered for an absent quantity',async()=>{
 const h=await harness(null,'NOT_APPLICABLE');assert.ok(!h.nodes().some(n=>n.tag==='button'&&n.text.startsWith('record.quantity')));
});
