import test from 'node:test';
import assert from 'node:assert/strict';
import {handle} from '../../deploy/cloudflare/worker.mjs';
const pass = 'synthetic-test-password-only-0123456789';
const token = 'synthetic-test-origin-only-0123456789';
const env = {PREVIEW_USER:'engineer',PREVIEW_PASSWORD:pass,ORIGIN_TOKEN:token,ORIGIN_URL:'https://backend.example',
  ASSETS:{fetch:async()=>new Response('<html>test</html>',{headers:{'Content-Type':'text/html'}})}};
const auth = {'Authorization':'Basic '+btoa('engineer:'+pass)};
function req(path='/', headers={}, method='GET') { return new Request('https://preview.pages.dev'+path,{method,headers:{...auth,...headers}}); }

test('missing configuration is closed',async()=>assert.equal((await handle(req(),{})).status,503));
test('all paths require password',async()=>{
  for (const path of ['/','/assets/app.js','/api/projects']) assert.equal((await handle(req(path,{'Authorization':''}),env)).status,401);
});
test('bad base64 is rejected',async()=>assert.equal((await handle(req('/',{'Authorization':'Basic %%'}),env)).status,401));
test('assets protected and no cache',async()=>{
  const r=await handle(req(),env);assert.equal(r.status,200);assert.equal(r.headers.get('Cache-Control'),'no-store');
});
test('unknown paths not exposed',async()=>assert.equal((await handle(req('/.env'),env)).status,404));
test('same origin write protection',async()=>{
  assert.equal((await handle(req('/api/projects',{},'POST'),env)).status,403);
  assert.equal((await handle(req('/api/projects',{'Origin':'https://evil.example','X-CIRP-Client':'browser'},'POST'),env)).status,403);
});
test('proxy strips browser credentials and uses fixed origin',async()=>{
  let called=false;
  const r=await handle(req('/api/projects',{'Cookie':'private=x','X-CIRP-Origin-Token':'attacker','X-CIRP-Client':'browser','Origin':'https://preview.pages.dev'},'POST'),env,async(url,init)=>{
    called=true;assert.equal(url,'https://backend.example/api/projects');
    assert.equal(init.headers.get('Authorization'),null);assert.equal(init.headers.get('Cookie'),null);
    assert.equal(init.headers.get('X-CIRP-Origin-Token'),token);assert.equal(init.headers.get('Origin'),'https://backend.example');
    assert.equal(init.redirect,'manual');return new Response('{}',{headers:{'Set-Cookie':'bad=x'}});
  });assert(called);assert.equal(r.status,200);assert.equal(r.headers.get('Set-Cookie'),null);
});
test('no redirect forwarding',async()=>assert.equal((await handle(req('/api/settings'),env,async()=>new Response(null,{status:302,headers:{Location:'https://elsewhere.example'}}))).status,502));
test('offline origin is explicit',async()=>assert.equal((await handle(req('/api/settings'),env,async()=>{throw new Error('down')})).status,502));
test('oversize rejected',async()=>assert.equal((await handle(req('/api/x',{'X-CIRP-Client':'browser','Content-Length':'10000000'},'POST'),env)).status,413));
test('unsafe origin configuration rejected',async()=>assert.equal((await handle(req('/api/settings'),{...env,ORIGIN_URL:'http://localhost:8000'})).status,503));
