import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const start=source.indexOf('function renderResumeEligibility(');
assert.ok(start>=0);
const renderer=source.slice(start,source.indexOf('function updateQuestionAvailability(',start));
for(const [label,run,unresolved,disabled] of [
 ['recoverable publication failure',{status:'FAILED',can_resume:true},[],false],
 ['backend denies paused run',{status:'PAUSED',can_resume:false},[],true],
 ['old backend fails closed',{status:'PAUSED'},[],true],
 ['nonboolean permission fails closed',{status:'PAUSED',can_resume:'true'},[],true],
 ['fresh unresolved call blocks stale permission',{status:'FAILED',can_resume:true},[{}],true],
]){
 test(label,()=>{
  const button={};const context=vm.createContext({$:()=>button,state:{unresolvedCalls:unresolved}});
  vm.runInContext(renderer,context);context.renderResumeEligibility(run);
  assert.equal(button.disabled,disabled);
 });
}

test('refresh delegates button eligibility to the shared renderer',()=>{
 assert.match(source,/renderResumeEligibility\(run\);/);
 assert.doesNotMatch(source,/\$\('resume'\)\.disabled=.*includes\(run.status\)/);
});
