const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../index.html'), 'utf8');
function source(name) {
  const start = html.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  return html.slice(start, html.indexOf('\n}', start) + 2);
}
const safeURL = vm.runInNewContext(`(${source('safeOfferURL')})`, { URL });
test('offer URLs reject executable protocols and malformed addresses', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/a', '', null, '/relative'])
    assert.equal(safeURL(value), '');
  assert.equal(safeURL('https://example.com/job?a=1&b=2'), 'https://example.com/job?a=1&b=2');
});
test('URL attributes cannot inject markup', () => {
  const escape = vm.runInNewContext(`(${source('escHtml')})`);
  const value = escape(safeURL('https://example.com/" onmouseover="alert(1)'));
  assert.ok(!value.includes('"'));
  assert.ok(!value.includes('<'));
});
function fetchHarness(fetch) {
  const context = vm.createContext({ fetch, AbortController, setTimeout, clearTimeout });
  vm.runInContext(`const activeRequests = new Set(); async ${source('fetchJSON')}`, context);
  return { run: (timeout=15) => vm.runInContext(`fetchJSON('/test', {}, ${timeout})`,context),
    pending: () => vm.runInContext('activeRequests.size', context) };
}
test('timeout cancels a stalled network request and clears tracking', async () => {
  const h = fetchHarness((url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'})))));
  await assert.rejects(h.run(), /trop de temps/);
  assert.equal(h.pending(),0);
});
test('timeout covers stalled JSON bodies too', async () => {
  const h=fetchHarness(async (url,{signal})=>({ok:true,json:()=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'}))))}));
  await assert.rejects(h.run(), /trop de temps/);
  assert.equal(h.pending(),0);
});
test('HTTP conflict is preserved for joining an existing refresh', async () => {
  const h=fetchHarness(async ()=>({ok:false,status:409}));
  await assert.rejects(h.run(), error=>error.status===409);
  assert.equal(h.pending(),0);
});
test('unavailable storage does not break the page', () => {
  const context=vm.createContext({localStorage:{getItem(){throw new Error('blocked')},setItem(){throw new Error('full')}}});
  vm.runInContext(`${source('readLocal')}\n${source('writeLocal')}`,context);
  assert.equal(vm.runInContext("readLocal('filters', 'fallback')",context),'fallback');
  assert.doesNotThrow(()=>vm.runInContext("writeLocal('filters', {})",context));
});
