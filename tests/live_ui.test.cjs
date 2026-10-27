const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup(enabled = true) {
  const nodes = Object.fromEntries(['video','loading','status','start','stop','reconnect','title','app','back'].map(id => ['#'+id,
    {textContent:'', disabled:false, hidden:false, classList:{toggle(){}}, pause(){}, load(){}, removeAttribute(){}}]));
  const events = {}, calls = [], replies = [];
  const ctx = vm.createContext({performance, URL, AbortController, location:{pathname:'/live/second'},
    setInterval(){}, setTimeout(fn, ms){const timer = setTimeout(fn,ms); timer.unref(); return timer;}, clearTimeout,
    window:{addEventListener(name,fn){events[name]=fn;}, MediaSource:class {}},
    document:{querySelector(selector){return nodes[selector];}, addEventListener(name,fn){events[name]=fn;}, createElement(){return {getContext(){return {};}};}},
    fetch:async (url, options) => {
      calls.push({url,options});
      if(url.endsWith('/info')) return {ok:true,json:async()=>({csrf:'csrf',enabled,message:enabled?null:'Capture unsupported.',device:{name:'Mi Box 3'},activity:{current:{name:'YouTube'},available:true}})};
      const result = replies.shift() || {message:'stopped'};
      return {ok:true,json:async()=>typeof result === 'function' ? result() : result};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/live.js'),'utf8'),ctx);
  return {nodes,events,calls,replies,ctx};
}
const ready = () => new Promise(resolve=>setImmediate(resolve));
test('device-specific live page reports unsupported capture without starting a process',async()=>{
  const app=setup(false); await ready();
  assert.equal(app.nodes['#title'].textContent,'Mi Box 3 · Live View');
  assert.equal(app.nodes['#back'].href,'/?tv=second');
  assert.equal(app.nodes['#start'].disabled,true);
  assert.match(app.nodes['#status'].textContent,/unsupported/);
  assert.equal(app.calls.length,1);
});
test('leaving during an in-flight start releases the returned viewer lease',async()=>{
  const app=setup(); await ready();
  let resolve;
  app.replies.push(()=>new Promise(done=>{resolve=done;}));
  const starting = app.nodes['#start'].onclick(); await ready();
  await app.events.pagehide();
  resolve({viewer:'test-viewer'}); await starting;
  const stopping=app.calls.find(call=>call.url.endsWith('/stop'));
  assert.ok(stopping);
  assert.deepEqual(JSON.parse(stopping.options.body),{viewer:'test-viewer'});
  assert.equal(stopping.options.keepalive,true);
});
