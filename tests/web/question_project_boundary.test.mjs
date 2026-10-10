/* Exercise the production project-question handler with an offline DOM surrogate. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const start=source.indexOf("$('question-form').onsubmit="),end=source.indexOf("$('reference-question').oninput",start);
assert.ok(start>=0&&end>start,'project question handler is required');
const handler=source.slice(start,end);

function harness(){
 const elements={
  'question-form':{},
  'project-question':{value:'What is the approved color?'},
  'project-question-profile':{value:''},
  'question-status':{textContent:''},
 };
 const state={project:'A',questionRequestVersion:0,knowledge:{available:true},referenceKnowledge:null,
              projectV9Preview:null,asking:false};
 const calls=[],rendered=[];let resolvePending,immediate=false,refreshes=0;
 const context=vm.createContext({
  state,$:id=>elements[id]||(elements[id]={}),v9Profile:id=>elements[id].value,
  v9ProofValid:()=>false,error:fn=>async(...args)=>fn(...args),updateQuestionAvailability(){},
  I:{bindText:(node,key)=>{node.textContent=key;return node;},t:key=>key},toast(){},
  renderQuestionAnswer:(question,result)=>rendered.push({question,result}),
  refreshRun:async()=>{refreshes+=1;},
  api:async(path,method,body)=>{calls.push({path,method,body});if(immediate)return {answer:'fresh'};
   return new Promise(resolve=>{resolvePending=resolve;});},
 });
 vm.runInContext(handler,context);
 return {context,state,elements,calls,rendered,resolve:value=>resolvePending(value),
         setImmediate:()=>{immediate=true;},refreshes:()=>refreshes};
}

test('A to B to A project switch cannot publish the older A response',async()=>{
 assert.match(source,/async function selectProject\(id\).*?state\.questionRequestVersion\+=1/s);
 const h=harness();
 const pending=h.elements['question-form'].onsubmit({preventDefault(){}});
 assert.equal(h.calls[0].path,'/projects/A/questions');

 // Simulate both selectProject transitions. Identity alone is insufficient after A -> B -> A.
 h.state.project='B';h.state.questionRequestVersion+=1;
 h.state.project='A';h.state.questionRequestVersion+=1;
 h.resolve({answer:'stale'});await pending;
 assert.deepEqual(h.rendered,[]);
 assert.equal(h.elements['project-question'].value,'What is the approved color?');
 assert.equal(h.refreshes(),0);

 h.setImmediate();
 await h.elements['question-form'].onsubmit({preventDefault(){}});
 assert.deepEqual(h.rendered,[{question:'What is the approved color?',result:{answer:'fresh'}}]);
 assert.equal(h.elements['project-question'].value,'');
 assert.equal(h.refreshes(),1);
});
