'use strict';
const device = decodeURIComponent(location.pathname.split('/').pop());
const base = '/api/live/' + encodeURIComponent(device);
const video = document.querySelector('#video'), loading = document.querySelector('#loading');
const statusText = document.querySelector('#status');
const startButton = document.querySelector('#start'), stopButton = document.querySelector('#stop'), reconnectButton = document.querySelector('#reconnect');
let csrf = '', viewer = null, running = false, enabled = false, generation = 0, controller = null, media = null, objectURL = null;
let lastFrame = 0, blackCount = 0, lastVideoTime = -1;
function status(text, error = false) { statusText.textContent = text; statusText.classList.toggle('error', error); }
function buttons() { startButton.disabled = running || !enabled; stopButton.disabled = !running; reconnectButton.disabled = !enabled; }
async function command(action, body = {}, keepalive = false) {
  const response = await fetch(base + '/' + action, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Remote-Token': csrf}, body: JSON.stringify(body), keepalive});
  const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Live request failed.'); return data;
}
async function stop(message = 'Stream stopped.') {
  generation++; running = false;
  if (controller) controller.abort();
  controller = null;
  const old = viewer; viewer = null;
  video.pause(); video.removeAttribute('src'); video.load();
  if (objectURL) URL.revokeObjectURL(objectURL);
  objectURL = null; media = null; loading.hidden = false; loading.textContent = message;
  buttons(); status(message);
  if (old) { try { await command('stop', {viewer: old}, true); } catch (_) { /* Lease timeout is the fallback. */ } }
}
function append(buffer, bytes) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => done(new Error('Browser video decoder timed out.')), 10000);
    const success = () => done(); const failed = () => done(new Error('Browser cannot decode this stream.'));
    function done(error) { clearTimeout(timer); buffer.removeEventListener('updateend', success); buffer.removeEventListener('error', failed); error ? reject(error) : resolve(); }
    buffer.addEventListener('updateend', success, {once: true}); buffer.addEventListener('error', failed, {once: true});
    try { buffer.appendBuffer(bytes); } catch (error) { done(error); }
  });
}
async function start() {
  if (running || !enabled) return;
  const Media = window.MediaSource || window.ManagedMediaSource;
  if (!Media) { status('This browser does not support Media Source video. Try a current Chrome or Safari browser.', true); return; }
  running = true; buttons(); const run = ++generation;
  loading.hidden = false; loading.textContent = 'Connecting to device…'; status('Starting capture…');
  try {
    const data = await command('start');
    if (run !== generation) { await command('stop', {viewer: data.viewer}, true); return; }
    viewer = data.viewer; controller = new AbortController();
    media = new Media(); objectURL = URL.createObjectURL(media); video.src = objectURL;
    await new Promise((resolve, reject) => { media.addEventListener('sourceopen', resolve, {once:true}); setTimeout(() => reject(new Error('Video player could not open.')), 10000); });
    if (run !== generation) return;
    let after = 0, buffer = null;
    lastFrame = performance.now(); lastVideoTime = -1; blackCount = 0;
    while (running && run === generation) {
      const response = await fetch(`${base}/media?viewer=${encodeURIComponent(viewer)}&after=${after}`, {signal: controller.signal});
      if (response.status === 204) continue;
      if (!response.ok) { const failure = await response.json(); throw new Error(failure.error || 'Stream ended.'); }
      if (!buffer) {
        const mime = `video/mp4; codecs="${response.headers.get('X-Video-Codec')}"`;
        if (!Media.isTypeSupported(mime)) throw new Error('This device’s H.264 profile is unsupported by your browser.');
        buffer = media.addSourceBuffer(mime);
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (run !== generation) return;
      await append(buffer, bytes);
      after = Number(response.headers.get('X-Sequence'));
      if (buffer.buffered.length) {
        const first = buffer.buffered.start(0), end = buffer.buffered.end(buffer.buffered.length - 1);
        if (video.currentTime < first || end - video.currentTime > 3) video.currentTime = Math.max(first, end - .5);
        if (video.paused) await video.play();
        if (video.currentTime > 15 && !buffer.updating) {
          await new Promise(resolve => { buffer.addEventListener('updateend', resolve, {once:true}); buffer.remove(0, video.currentTime - 10); });
        }
      }
    }
  } catch (error) {
    if (run !== generation) return;
    await stop('Stream unavailable.'); status(error.message, true);
  }
}
const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 18;
const context = canvas.getContext('2d', {willReadFrequently: true});
setInterval(() => {
  if (!running || !viewer) return;
  if (video.readyState >= 2 && video.videoWidth && video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime; lastFrame = performance.now(); loading.hidden = true;
    try {
      context.drawImage(video, 0, 0, 32, 18);
      const pixels = context.getImageData(0, 0, 32, 18).data;
      let brightness = 0; for (let i=0; i<pixels.length; i+=4) brightness += pixels[i] + pixels[i+1] + pixels[i+2];
      blackCount = brightness / (32 * 18 * 3) < 5 ? blackCount + 1 : 0;
      status(blackCount >= 3 ? 'Black frames detected. Capture may be unsupported, protected, or showing a dark scene.' : 'Video frames received.', blackCount >= 3);
    } catch (_) { status('Video frames received.'); }
  } else if (lastFrame && performance.now() - lastFrame > 20000) {
    stop('No decoded video frames. Capture or browser codec support may be unavailable.');
  }
}, 1000);
async function loadInfo() {
  const response = await fetch(base + '/info'); const info = await response.json();
  if (!response.ok) throw new Error(info.error || 'Cannot load device.');
  csrf = info.csrf; enabled = info.enabled && !info.message;
  document.querySelector('#title').textContent = info.device.name + ' · Live View';
  document.querySelector('#app').textContent = 'Foreground app: ' + (info.activity.current?.name || 'not observed') + (info.activity.available ? '' : ' (last known)');
  if (!running) { loading.textContent = info.message || 'Ready to start'; status(info.message || 'Press Start Stream to connect.'); }
  buttons();
}
document.querySelector('#back').href = '/?tv=' + encodeURIComponent(device);
startButton.onclick = start; stopButton.onclick = () => stop();
reconnectButton.onclick = async () => { await stop('Reconnecting…'); await new Promise(resolve => setTimeout(resolve, 1000)); await start(); };
window.addEventListener('pagehide', () => stop());
document.addEventListener('visibilitychange', () => { if (document.hidden) stop('Paused while this page is inactive. Press Start Stream to resume.'); });
setInterval(() => { if (!document.hidden) loadInfo().catch(() => {}); }, 15000);
loadInfo().catch(error => { loading.textContent = 'Unavailable'; status(error.message, true); });
