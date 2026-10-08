import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const start=source.indexOf('async function uploadFiles('),end=source.indexOf("$('new-project').onclick",start);
assert.ok(start>=0&&end>start,'uploadFiles boundary is required');
const uploader=source.slice(start,end);

function harness({switchProject=false}={}){
 const roots={start:{disabled:false},'upload-progress':{textContent:''}},calls=[],sets=[],removes=[];
 const state={project:'A',uploading:false,settings:{upload_chunk_bytes:1024}};let refreshes=0,next=0;
 const context=vm.createContext({state,$:id=>roots[id],toast(){},I:{bindText:(node,value)=>{node.textContent=typeof value==='function'?value():value;return node;},message:value=>value},
  localStorage:{getItem(){return null;},setItem(key,value){sets.push({key,value});},removeItem(key){removes.push(key);}},
  api:async(path,method,body)=>{calls.push({path,method,body});if(path.startsWith('/projects/')){next+=1;if(switchProject&&next===1)state.project='B';return {id:`u${next}`,offset:0,chunks:[]};}if(path.endsWith('/complete'))return {document_id:`d${next}`};throw Error(`unexpected ${path}`);},
  refresh:async()=>{refreshes+=1;}});
 vm.runInContext(uploader,context);
 return {context,state,calls,sets,removes,refreshes:()=>refreshes};
}
const files=[{name:'one.pdf',size:0,lastModified:1},{name:'two.pdf',size:0,lastModified:2}];

test('captured upload project survives a project switch during the first request',async()=>{
 const h=harness({switchProject:true});await h.context.uploadFiles(files);
 assert.deepEqual(h.calls.filter(call=>call.path.startsWith('/projects/')).map(call=>call.path),['/projects/A/uploads','/projects/A/uploads']);
 assert.ok(h.sets.length===2&&h.sets.every(entry=>entry.key.startsWith('cirp-upload:A:')));
 assert.equal(h.refreshes(),0);assert.equal(h.state.project,'B');
});

test('unchanged project refreshes once after a normal captured batch',async()=>{
 const h=harness();await h.context.uploadFiles(files);
 assert.equal(h.refreshes(),1);assert.ok(h.sets.every(entry=>entry.key.startsWith('cirp-upload:A:')));
});
