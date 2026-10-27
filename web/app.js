'use strict';
const dropdown = document.querySelector('#device-select');
const feedback = document.querySelector('#feedback');
const powerState = document.querySelector('#power-state');
const controls = document.querySelectorAll('[data-action]');
let token = '', selected = null, busy = false, devices = [];
function say(text, error = false) { feedback.textContent = text; feedback.classList.toggle('error', error); }
function render() {
  dropdown.disabled = busy || devices.length === 0;
  controls.forEach(button => button.disabled = busy || !selected);
}
function disconnected(status = 'Not connected') {
  selected = null; dropdown.value = ''; powerState.textContent = status;
}
async function request(tv, action) {
  const response = await fetch('/api/command', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Remote-Token': token}, body: JSON.stringify({tv, action})});
  const data = await response.json();
  if (!response.ok) {
    if (data.offline) disconnected('Offline');
    throw new Error(data.error || 'Command failed.');
  }
  return data;
}
dropdown.onchange = async () => {
  if (busy) return;
  const device = devices.find(device => device.id === dropdown.value);
  selected = null;
  if (!device) { disconnected(); say('Choose a TV above.'); render(); return; }
  busy = true; render(); powerState.textContent = 'Checking…'; say(`Checking ${device.name}…`);
  try {
    const result = await request(device.id, 'select'); selected = device;
    powerState.textContent = result.status; say(`${device.name} connected.`);
  } catch (error) { disconnected('Offline'); say(error.message, true); }
  finally { busy = false; render(); }
};
controls.forEach(button => button.onclick = async () => {
  if (busy || !selected) return;
  busy = true; render(); const started = performance.now(); say('Sending…');
  try {
    const result = await request(selected.id, button.dataset.action);
    if (result.status) powerState.textContent = result.status;
    else if (['on', 'off'].includes(button.dataset.action)) powerState.textContent = 'Refresh to check';
    say(result.status ? `Current state: ${result.status}` : `Sent · ${Math.round(performance.now() - started)} ms`);
  } catch (error) { say(error.message, true); }
  finally { busy = false; render(); }
});
async function init() {
  try {
    const response = await fetch('/api/devices');
    if (!response.ok) throw new Error('Cannot load devices. Reload this page.');
    const data = await response.json(); token = data.csrf; devices = data.devices;
    for (const device of devices) {
      const option = document.createElement('option'); option.value = device.id;
      option.textContent = device.name; dropdown.append(option);
    }
    say(devices.length ? 'Choose a TV above to connect.' : 'No devices configured.');
  } catch (error) { say(error.message, true); }
  finally { render(); }
}
render();
init();
