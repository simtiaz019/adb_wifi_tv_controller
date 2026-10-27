// Dependency-free UI behavior checks; no browser or real ADB/Telegram calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup() {
  function element() {
    return {textContent: '', hidden: false, disabled: false, children: [], dataset: {},
      classList: {toggle() {}}, append(child) { this.children.push(child); }};
  }
  const names = ['picker', 'remote', 'feedback', 'switch', 'devices', 'device-name', 'power-state'];
  const nodes = Object.fromEntries(names.map(n => ['#' + n, element()]));
  nodes['#remote'].hidden = true;
  const actions = ['home', 'ok', 'off', 'volup', 'status'].map(action => Object.assign(element(), {dataset: {action}}));
  const calls = [];
  const responses = [];
  const context = vm.createContext({performance, document: {
    querySelector: selector => nodes[selector],
    querySelectorAll: selector => selector === '[data-action]' ? actions : [...actions, nodes['#switch'], ...nodes['#devices'].children],
    createElement: element
  }, fetch: async (url, options) => {
    calls.push({url, options});
    if (url === '/api/devices') return {ok: true, json: async () => ({csrf: 'test-token', devices: [
      {id: 'main', name: 'Haier TV', ip: '192.168.1.243'}, {id: 'second', name: 'Mi Box 3', ip: '192.168.1.149'}]})};
    const result = responses.shift() || {status: 'ON'};
    return {ok: !result.error, json: async () => result};
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  return {nodes, actions, calls, responses};
}
const ready = () => new Promise(resolve => setImmediate(resolve));
test('select device, send direct home key, switch device', async () => {
  const app = setup(); await ready();
  assert.equal(app.nodes['#devices'].children.length, 2);
  await app.nodes['#devices'].children[1].onclick();
  assert.equal(app.nodes['#device-name'].textContent, 'Mi Box 3');
  assert.equal(app.nodes['#remote'].hidden, false);
  app.responses.push({message: 'sent'});
  await app.actions[0].onclick();
  const request = app.calls.at(-1);
  assert.deepEqual(JSON.parse(request.options.body), {tv: 'second', action: 'home'});
  assert.equal(request.options.headers['X-Remote-Token'], 'test-token');
  assert.match(app.nodes['#feedback'].textContent, /Sent · \d+ ms/);
  app.nodes['#switch'].onclick();
  assert.equal(app.nodes['#remote'].hidden, true);
  assert.equal(app.nodes['#picker'].hidden, false);
});
test('offline device and failed commands return to picker', async () => {
  const app = setup(); await ready();
  app.responses.push({error: 'Mi Box 3 is not online', offline: true});
  await app.nodes['#devices'].children[1].onclick();
  assert.equal(app.nodes['#remote'].hidden, true);
  assert.match(app.nodes['#feedback'].textContent, /not online/);
  await app.nodes['#devices'].children[0].onclick();
  app.responses.push({error: 'Command was not confirmed', offline: true});
  await app.actions[1].onclick();
  assert.equal(app.nodes['#remote'].hidden, true);
  assert.equal(app.nodes['#picker'].hidden, false);
  assert.equal(app.actions[1].disabled, false);
  const count = app.calls.length;
  await app.actions[1].onclick();
  assert.equal(app.calls.length, count);
});
