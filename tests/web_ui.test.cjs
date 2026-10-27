// Dependency-free UI behavior checks; no browser or real ADB/Telegram calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup() {
  function element() {
    return {textContent: '', value: '', disabled: false, children: [], dataset: {},
      classList: {toggle() {}}, append(child) { this.children.push(child); }};
  }
  const nodes = Object.fromEntries(['device-select', 'feedback', 'power-state'].map(n => ['#' + n, element()]));
  const actions = ['home', 'ok', 'off', 'volup', 'status'].map(action => Object.assign(element(), {dataset: {action}}));
  const calls = [], responses = [];
  const context = vm.createContext({performance, document: {
    querySelector: selector => nodes[selector],
    querySelectorAll: () => actions,
    createElement: element
  }, fetch: async (url, options) => {
    calls.push({url, options});
    if (url === '/api/devices') return {ok: true, json: async () => ({csrf: 'test-token', devices: [
      {id: 'main', name: 'Haier TV'}, {id: 'second', name: 'Mi Box 3'}, {id: 'tv3', name: 'Bedroom TV'}]})};
    let result = responses.shift() || {status: 'ON'};
    if (typeof result === 'function') result = await result();
    return {ok: !result.error, json: async () => result};
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  const choose = async id => { nodes['#device-select'].value = id; await nodes['#device-select'].onchange(); };
  return {nodes, actions, calls, responses, choose};
}
const ready = () => new Promise(resolve => setImmediate(resolve));
test('dropdown switches devices and targets commands without changing screens', async () => {
  const app = setup(); await ready();
  assert.equal(app.nodes['#device-select'].children.length, 3);
  assert.equal(app.nodes['#device-select'].disabled, false);
  assert.ok(app.actions.every(button => button.disabled));
  await app.choose('second');
  assert.equal(app.nodes['#power-state'].textContent, 'ON');
  assert.ok(app.actions.every(button => !button.disabled));
  app.responses.push({message: 'sent'});
  await app.actions[0].onclick();
  const request = app.calls.at(-1);
  assert.deepEqual(JSON.parse(request.options.body), {tv: 'second', action: 'home'});
  assert.equal(request.options.headers['X-Remote-Token'], 'test-token');
  assert.match(app.nodes['#feedback'].textContent, /Sent · \d+ ms/);
  await app.choose('tv3');
  await app.actions[1].onclick();
  assert.deepEqual(JSON.parse(app.calls.at(-1).options.body), {tv: 'tv3', action: 'ok'});
});
test('offline device disables controls and leaves dropdown available for retry', async () => {
  const app = setup(); await ready();
  await app.choose('main');
  app.responses.push({error: 'Mi Box 3 is not online', offline: true});
  await app.choose('second');
  assert.equal(app.nodes['#device-select'].value, '');
  assert.equal(app.nodes['#device-select'].disabled, false);
  assert.match(app.nodes['#feedback'].textContent, /not online/);
  assert.ok(app.actions.every(button => button.disabled));
  const count = app.calls.length;
  await app.actions[1].onclick();
  assert.equal(app.calls.length, count);
  await app.choose('second');
  app.responses.push({error: 'Command was not confirmed', offline: true});
  await app.actions[1].onclick();
  assert.equal(app.nodes['#power-state'].textContent, 'Offline');
  assert.ok(app.actions.every(button => button.disabled));
});
test('pending selection blocks commands and empty selection clears old target', async () => {
  const app = setup(); await ready();
  await app.choose('main');
  let finish;
  app.responses.push(() => new Promise(resolve => { finish = resolve; }));
  const switching = app.choose('second');
  assert.ok(app.actions.every(button => button.disabled));
  assert.equal(app.nodes['#device-select'].disabled, true);
  const count = app.calls.length;
  await app.actions[0].onclick();
  assert.equal(app.calls.length, count);
  finish({status: 'OFF'}); await switching;
  assert.equal(app.nodes['#power-state'].textContent, 'OFF');
  await app.choose('');
  assert.equal(app.nodes['#power-state'].textContent, 'Not connected');
  assert.ok(app.actions.every(button => button.disabled));
});
