import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../web/app.js',import.meta.url),'utf8');
const publicationStart=source.indexOf('function renderPublicationIssues(');
const recordStart=source.indexOf('async function showRecord(');
const evidenceStart=source.indexOf('async function showEvidence(',recordStart);
const verificationStart=source.indexOf('function verificationPanel(');
const citationStart=source.indexOf('async function showCitation(',verificationStart);
assert.ok(publicationStart>=0,'publication issue renderer is required');
assert.ok(recordStart>=0&&evidenceStart>recordStart&&verificationStart>=0&&citationStart>verificationStart);
const publicationRenderer=source.slice(publicationStart,source.indexOf('function updateQuestionAvailability(',publicationStart));
const recordsStart=source.indexOf('function renderRecords('),takeoffsStart=source.indexOf('function renderTakeoffs(',recordsStart);
const recordRenderer=source.slice(recordStart,evidenceStart);
const verificationRenderer=source.slice(verificationStart,citationStart);
assert.ok(recordsStart>=0&&takeoffsStart>recordsStart);
const recordsRenderer=source.slice(recordsStart,takeoffsStart);

function node(tag,text=null,cls=''){
 return {tag,text,cls,children:[],dataset:{},disabled:false,hidden:false,attrs:{},
  append(...items){this.children.push(...items);},replaceChildren(...items){this.children=[];this.append(...items);},
  setAttribute(key,value){this.attrs[key]=value;},remove(){this.removed=true;}};
}
function all(root){return [root,...root.children.flatMap(all)];}
function i18n(){return {t:(key,args={})=>`${key}:${JSON.stringify(args)}`,status:value=>value,
 bindText:(item,key,args={})=>{item.text=typeof key==='function'?key():`${key}:${JSON.stringify(args)}`;return item;},
 bindStatus:(item,value)=>{item.text=value;return item;},applyElement(){}};}

test('run publication issues visibly expose location fields and source buttons as text nodes',()=>{
 const roots={'publication-issues':node('section'),'publication-issues-summary':node('p'),'publication-issues-list':node('div')};const evidence=[];
 const issue={issue_id:'issue-1',kind:'MATERIAL',candidate_key:'<img src=x onerror=alert(1)>',evidence_ids:['source-1','source-2'],instance_path:'/candidate/quantity',schema_path:'/quantity',validator:'candidate-schema'};
 const context=vm.createContext({$:id=>roots[id],el:node,elT:(tag,key,args={},cls='')=>node(tag,`${key}:${JSON.stringify(args)}`,cls),I:i18n(),error:fn=>fn,showEvidence:(id,_range,runId)=>evidence.push({id,runId})});
 vm.runInContext(publicationRenderer,context);context.renderPublicationIssues({id:'run-1',coverage:{publication_issues:[issue]}});
 assert.equal(roots['publication-issues'].hidden,false);
 const nodes=all(roots['publication-issues-list']);assert.ok(nodes.some(item=>item.text?.includes('publicationIssues.issue')));
 assert.ok(nodes.some(item=>item.text?.includes('<img src=x onerror=alert(1)>')));
 assert.equal(nodes.some(item=>Object.hasOwn(item,'innerHTML')),false);
 const buttons=nodes.filter(item=>item.tag==='button');assert.equal(buttons.length,2);buttons.forEach(button=>button.onclick());
 assert.deepEqual(evidence,[{id:'source-1',runId:'run-1'},{id:'source-2',runId:'run-1'}]);
});

test('historical blocked record disables review, quantity, and verification actions',async()=>{
 const roots={'drawer':node('div'),'drawer-title':node('h2'),'drawer-body':node('section')};const calls=[];
 const row={publication_blocked:true,publication_issues:[{issue_id:'issue-1',candidate_key:'PANEL_A1'}],review_version:4,
  record:{kind:'MATERIAL',meta:{record_id:'old-record'},review:{status:'ACCEPTED'},quantity_review:'VERIFIED',candidate:{candidate_key:'PANEL_A1',name:'PANEL_A1',quantity:{value:2,unit:'EA'},design_properties:[],evidence_ids:[]}},
  verification:{status:'NOT_CHECKED',fields:[]}};
 const context=vm.createContext({$:id=>roots[id],el:node,elT:(tag,key,args={},cls='')=>node(tag,`${key}:${JSON.stringify(args)}`,cls),labels:{MATERIAL:'Material'},I:i18n(),sourceIds:()=>[],error:fn=>fn,toast(){},refreshRun:async()=>{},state:{settings:{live_ready:true},records:[]},api:async(...args)=>{calls.push(args);return {};}});
 vm.runInContext(verificationRenderer+'\n'+recordRenderer,context);await context.showRecord(row);
 const nodes=all(roots['drawer-body']);assert.ok(nodes.some(item=>item.text?.startsWith('publicationIssues.historicalRecord')));
 const buttons=nodes.filter(item=>item.tag==='button');const protectedActions=buttons.filter(item=>/^(record\.(accept|edit|reject|quantity)|verify\.(check|semantic))/.test(item.text||''));
 assert.ok(protectedActions.length>=8);assert.ok(protectedActions.every(item=>item.disabled));
 assert.equal(calls.length,0);
});

test('result list labels a blocked historical record instead of only its old accepted review',()=>{
 const roots={'count-MATERIAL':node('strong'),'count-INSPECTION':node('strong'),'filter':{value:''},'results-body':node('tbody'),'empty':node('p'),'results-page-status':node('small'),'results-load-more':node('button')};
 const row={publication_blocked:true,verification:{status:'STALE'},record:{kind:'MATERIAL',meta:{record_id:'old-record'},review:{status:'ACCEPTED'},quantity_review:'VERIFIED',candidate:{name:'PANEL_A1',quantity:{value:2,unit:'EA'},design_properties:[],evidence_ids:[]}}};
 const context=vm.createContext({state:{recordCounts:{MATERIAL:1,INSPECTION:0},kind:'MATERIAL',records:[row],recordPagination:null},labels:{MATERIAL:'kind.MATERIAL',INSPECTION:'kind.INSPECTION'},$:id=>roots[id],document:{querySelectorAll:()=>[]},el:node,elT:(tag,key,args={},cls='')=>node(tag,`${key}:${JSON.stringify(args)}`,cls),I:i18n(),sourceIds:()=>[],error:fn=>fn,showEvidence(){},showRecord(){}});
 vm.runInContext(recordsRenderer,context);context.renderRecords();
 assert.ok(all(roots['results-body']).some(item=>item.text?.startsWith('publicationIssues.blockedList')));
});
