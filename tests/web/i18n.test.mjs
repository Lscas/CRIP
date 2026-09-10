/* Offline, dependency-free display contract tests. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../web/i18n.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('../../web/index.html',import.meta.url),'utf8');
const catalog=JSON.parse(source.match(/const catalog = ([\s\S]*?);\n  const statusCatalog/)[1]);
const statusCatalog=JSON.parse(source.match(/const statusCatalog = ([\s\S]*?);\n  const systemEnglish/)[1]);
const nodes=[];
function node(attrs={}) { const result={attrs,isConnected:true,textContent:'',value:'',dataset:{},events:{},
 getAttribute(k){return this.attrs[k]??null;},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;}};nodes.push(result);return result; }
function setup(saved,blocked=false){
 nodes.length=0;
 const writes=[];const store=new Map(saved===undefined?[]:[['cirp.ui.language.v1',saved]]);
 const label=node({'data-i18n':'project.new'});const input=node({'data-i18n-placeholder':'filter.placeholder','data-i18n-aria-label':'filter.label'});
 input.value='Keep my edit';const selectors=[node(),node(),node()];
 const doc={documentElement:{lang:''},querySelectorAll:q=>q==='[data-ui-language]'?selectors:q==='[data-i18n-bound]'?nodes.filter(n=>n.isConnected&&Object.hasOwn(n.attrs,'data-i18n-bound')):[label,input]};
 const context=vm.createContext({document:doc,localStorage:{getItem(k){if(blocked)throw Error('blocked');return store.get(k)??null;},setItem(k,v){if(blocked)throw Error('blocked');writes.push([k,v]);store.set(k,v);}},fetch(){throw Error('Localization must not call the network');}});
 vm.runInContext(source,context);context.CIRPI18n.init();
 return {I:context.CIRPI18n,doc,label,input,selectors,writes,store};
}
test('default language is Chinese, regardless of browser language',()=>{const x=setup();assert.equal(x.I.language,'zh-CN');assert.equal(x.label.textContent,'＋ 新建项目');});
test('stored English is restored at initialization',()=>{const x=setup('en');assert.equal(x.doc.documentElement.lang,'en');assert.equal(x.label.textContent,'＋ New project');});
test('unsupported persisted value falls back to Chinese',()=>assert.equal(setup('bad').I.language,'zh-CN'));
test('missing storage never prevents switching',()=>{const x=setup(undefined,true);assert.doesNotThrow(()=>x.I.setLanguage('en'));assert.equal(x.label.textContent,'＋ New project');});
test('switch only writes a namespaced display preference',()=>{const x=setup();x.I.setLanguage('en');assert.deepEqual(x.writes,[['cirp.ui.language.v1','en']]);});
test('unsupported selection does not write or change display',()=>{const x=setup();assert.equal(x.I.setLanguage('de'),false);assert.equal(x.writes.length,0);assert.equal(x.I.language,'zh-CN');});
test('header, editor and dialog selectors stay synchronized',()=>{const x=setup();x.selectors[2].value='en';x.selectors[2].events.change();assert.ok(x.selectors.every(s=>s.value==='en'));});
test('placeholder and aria-label change, input value stays intact',()=>{const x=setup();x.I.setLanguage('en');assert.equal(x.input.attrs.placeholder,'Filter current results…');assert.equal(x.input.attrs['aria-label'],'Filter results');assert.equal(x.input.value,'Keep my edit');});
test('parameters retain exact numeric values, currency and scale',()=>{const x=setup('en');assert.equal(x.I.t('run.cost',{spent:'12.3400',reserved:'1.0000'}),'Recorded API cost ¥12.3400 · Reserved ¥1.0000 / ¥300');});
test('parameter replacement treats HTML as literal text',()=>{const x=setup('en');assert.equal(x.I.t('record.source',{id:'<img onerror=x>'}),'View source <img onerror=x>');});
test('raw document text is never auto-translated',()=>{const x=setup('en');const n=node();x.I.bindText(n,()=> '项目名：混凝土 / Concrete');x.I.setLanguage('zh-CN');assert.equal(n.textContent,'项目名：混凝土 / Concrete');});
test('enum labels change without changing canonical codes',()=>{const x=setup();const n=node();x.I.bindStatus(n,'PARTIAL');x.I.setLanguage('en');assert.equal(n.dataset.code,'PARTIAL');assert.equal(n.textContent,'Partial (PARTIAL)');});
test('unknown statuses and inherited property names remain literal',()=>{const x=setup('en');assert.equal(x.I.status('FUTURE_STATUS'),'FUTURE_STATUS');assert.equal(x.I.status('constructor'),'constructor');assert.equal(x.I.t('toString'),'toString');});
test('binding replacement retains the latest value across switches',()=>{const x=setup();const n=node();x.I.bindText(n,'project.none');x.I.bindText(n,()=> '工程 A');x.I.setLanguage('en');assert.equal(n.textContent,'工程 A');});
test('detached bindings are safely discarded',()=>{const x=setup();const n=node();x.I.bindText(n,'run.start');n.isConnected=false;x.I.setLanguage('en');assert.equal(n.textContent,'开始分析');});
test('known system notices translate and restore',()=>{const x=setup();const n=node();x.I.bindMessage(n,'审核记录已保存');x.I.setLanguage('en');assert.equal(n.textContent,'Review saved');x.I.setLanguage('zh-CN');assert.equal(n.textContent,'审核记录已保存');});
test('unknown errors and quoted original data remain unchanged',()=>{const x=setup('en');assert.equal(x.I.message('原文件 甲.pdf 无此设计'),'原文件 甲.pdf 无此设计');});
test('known backend validation lists are translated',()=>{const x=setup('en');assert.equal(x.I.message('未配置 API 密钥；付费 API 开关未开启'),'API key is not configured; Live API switch is disabled');});
test('dynamic capacity and class-name diagnostics preserve values',()=>{const x=setup('en');assert.equal(x.I.message('超出当前项目接收容量配置（104,857,600 bytes）'),'Project upload capacity exceeded (104,857,600 bytes)');assert.equal(x.I.message('内部处理错误：ValueError'),'Internal processing error: ValueError');});
test('all static HTML translation keys exist in both languages',()=>{for(const m of html.matchAll(/data-i18n(?:-initial|-placeholder|-aria-label|-title)?="([^"]+)"/g)){assert.ok(Object.hasOwn(catalog.en,m[1]),m[1]);assert.ok(Object.hasOwn(catalog['zh-CN'],m[1]),m[1]);}});
test('catalog keys and placeholders match across languages',()=>{assert.deepEqual(Object.keys(catalog.en).sort(),Object.keys(catalog['zh-CN']).sort());for(const key of Object.keys(catalog.en)){const params=s=>[...s.matchAll(/\{\w+\}/g)].map(x=>x[0]).sort();assert.deepEqual(params(catalog.en[key]),params(catalog['zh-CN'][key]),key);assert.ok(catalog.en[key].length>0,key);}assert.deepEqual(Object.keys(statusCatalog.en).sort(),Object.keys(statusCatalog['zh-CN']).sort());});
test('localization script has no network, HTML injection, reload or translation API',()=>{assert.doesNotMatch(source,/\bfetch\s*\(|XMLHttpRequest|innerHTML\s*=|location\.(reload|assign|replace)\s*\(/);});

test('removed polling rows are not strongly retained by the localization registry',()=>assert.match(source,/const bindings = new WeakMap\(\)/));
