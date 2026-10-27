'use strict';
const picker = document.querySelector('#picker');
const panel = document.querySelector('#remote');
const feedback = document.querySelector('#feedback');
let token = '', selected = null, busy = false;
function say(text, error = false) { feedback.textContent = text; feedback.classList.toggle('error', error); }
function home() { selected = null; picker.hidden = false; panel.hidden = true; }
function pending(value) { busy = value; document.querySelectorAll('button').forEach(b => b.disabled = value); }
async function request(tv, action) {
  const response = await fetch('/api/command', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Remote-Token': token}, body: JSON.stringify({tv, action})});
  const data = await response.json();
  if (!response.ok) { if (data.offline) home(); throw new Error(data.error || 'Command failed.'); }
  return data;
}
async function select(device) {
  if (busy) return;
  pending(true); say(`Checking ${device.name}…`);
  try {
    const result = await request(device.id, 'select'); selected = device;
    document.querySelector('#device-name').textContent = device.name;
    document.querySelector('#power-state').textContent = result.status;
    picker.hidden = true; panel.hidden = false; say('Connected. Ready when you are.');
  } catch (error) { home(); say(error.message, true); }
  finally { pending(false); }
}
document.querySelector('#switch').onclick = () => { home(); say('Choose a device.'); };
document.querySelectorAll('[data-action]').forEach(button => button.onclick = async () => {
  if (busy || !selected) return;
  pending(true); const started = performance.now(); say('Sending…');
  try {
    const result = await request(selected.id, button.dataset.action);
    if (result.status) document.querySelector('#power-state').textContent = result.status;
    else if (['on', 'off'].includes(button.dataset.action)) document.querySelector('#power-state').textContent = 'Status not refreshed';
    say(result.status ? `Current state: ${result.status}` : `Sent · ${Math.round(performance.now() - started)} ms`);
  } catch (error) { say(error.message, true); }
  finally { pending(false); }
});
async function init() {
  try {
    const response = await fetch('/api/devices');
    if (!response.ok) throw new Error('Cannot load devices. Reload this page.');
    const data = await response.json(); token = data.csrf;
    for (const device of data.devices) {
      const button = document.createElement('button'); button.className = 'device-card';
      for (const [tag, text, cls] of [['span','▣','device-icon'], ['strong',device.name,''], ['small',device.ip,''], ['span','Open remote →','open']]) {
        const element = document.createElement(tag); element.textContent = text; element.className = cls; button.append(element);
      }
      button.onclick = () => select(device); document.querySelector('#devices').append(button);
    }
    say('Choose a device to connect.');
  } catch (error) { say(error.message, true); }
}
init();
