'use strict';
const $ = (selector) => document.querySelector(selector);
const names = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const noteName = (key) => `${names[key % 12]}${Math.floor(key / 12) - 1}`;
const typingCodes = ['KeyA','KeyW','KeyS','KeyE','KeyD','KeyF','KeyT','KeyG','KeyY','KeyH','KeyU','KeyJ','KeyK','KeyO','KeyL','KeyP','Semicolon'];
let player, context, catalog, midiAccess, loading = false, refreshing = false, syncing = false, pendingRefresh = false, typingBase = 48, projectToken;
const pointers = new Map(), typed = new Map(), cards = new Map();
let preferences = {stops:{}};
try { preferences = JSON.parse(sessionStorage.getItem('loop-player-settings')) || preferences; } catch (_) {}
if (!preferences || typeof preferences !== 'object' || !preferences.stops) preferences = {stops:{}};
function remember() {
  for (const [id, card] of cards) preferences.stops[id] = {enabled:player ? !!player.stops.get(id)?.enabled : !!preferences.stops[id]?.enabled, level:Number(card.querySelector('input').value)};
  for (const key of ['volume','release','extension']) preferences[key] = Number($('#' + key).value);
  try { sessionStorage.setItem('loop-player-settings', JSON.stringify(preferences)); } catch (_) {}
}
for (const key of ['volume','release','extension']) if (Number.isFinite(preferences[key])) $('#' + key).value = preferences[key];
$('#volume-value').textContent = `${$('#volume').value}%`;
$('#release-value').textContent = `${(Number($('#release').value)/1000).toFixed(2)} s`;
const syncLabels = {current:'Up to date', changed:'Project has newer edits', unlinked:'Independent snapshot - choose a project to link', unknown:'Update to compare with the saved project', missing:'Project unavailable - snapshot still playable'};

function status(message, error = false) { $('#status').textContent = message; $('#status').classList.toggle('error', error); }
async function request(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Loop-Workspace': '1', 'X-Loop-Project': projectToken}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The local server could not complete the request.');
  return result;
}
function update() {
  if (!player) return;
  const enabled = [...player.stops.values()].filter(s => s.enabled);
  $('#registration').textContent = `${enabled.length} drawn`;
  $('#voices').textContent = `${[...player.voices].filter(v => !v.ending).length} voices`;
  const notes = new Set([...player.held.values()].map(h => h.note));
  for (const key of document.querySelectorAll('.key')) {
    const note = Number(key.dataset.note);
    key.classList.toggle('held', notes.has(note));
    key.setAttribute('aria-pressed', String(notes.has(note)));
    key.classList.toggle('unmapped', !enabled.some(stop => player.sampleFor(stop, note)));
  }
  for (const [id, card] of cards) {
    const stop = player.stops.get(id);
    card.classList.toggle('drawn', !!stop?.enabled);
    card.querySelector('.stop-toggle').setAttribute('aria-pressed', String(!!stop?.enabled));
  }
}
function buildStops() {
  $('#stops').replaceChildren(); cards.clear();
  if (!catalog.stops.length) {
    const empty = document.createElement('p'); empty.className = 'empty';
    empty.textContent = 'Your instrument starts with a register. Prepare note samples in the workspace, then use “Add a register” below.';
    $('#stops').append(empty); return;
  }
  for (const stop of catalog.stops) {
    const card = document.createElement('article'); card.className = 'stop-card';
    const toggle = document.createElement('button'); toggle.className = 'stop-toggle';
    toggle.setAttribute('aria-pressed', 'false'); toggle.setAttribute('aria-label', stop.name);
    const knob = document.createElement('span'); knob.className = 'stop-knob'; knob.textContent = '●'; knob.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span'), title = document.createElement('span'), subtitle = document.createElement('span');
    title.className = 'stop-title'; title.textContent = stop.name;
    subtitle.className = 'stop-subtitle'; subtitle.textContent = `${stop.samples.length} pipes · ${noteName(stop.samples[0].key)}–${noteName(stop.samples.at(-1).key)}`;
    text.append(title, subtitle); toggle.append(knob, text);
    toggle.onclick = async () => {
      if (loading || refreshing) return;
      if (!player) { status('Start audio first to load the instrument.'); return; }
      try {
        await context.resume();
        player.enable(stop.id, !player.stops.get(stop.id).enabled); remember();
        status('Ready to play. Draw any combination of stops.');
      } catch (error) { status(error.message, true); }
    };
    const level = document.createElement('label'); level.className = 'stop-level'; level.append('LEVEL');
    const slider = document.createElement('input'); slider.type = 'range'; slider.min = 0; slider.max = 100; slider.value = preferences.stops[stop.id]?.level ?? 70;
    slider.setAttribute('aria-label', `${stop.name} level`);
    const output = document.createElement('output'); output.textContent = `${slider.value}%`;
    slider.oninput = () => { output.textContent = `${slider.value}%`; const s = player?.stops.get(stop.id); if (s) s.gain.gain.setTargetAtTime(Number(slider.value) / 100, context.currentTime, .02); remember(); };
    level.append(slider, output);
    const review = document.createElement('p'); review.className = 'review-note';
    const drafts = stop.samples.filter(s => !s.reviewed || s.testApproved).length;
    review.textContent = drafts ? `${drafts} draft pipes · listening review needed` : 'Listening review complete';
    const connection = document.createElement('p'); connection.className = 'project-connection';
    connection.textContent = `${stop.projectName} - ${syncLabels[stop.syncState] || 'Snapshot'}`;
    const actions = document.createElement('div'); actions.className = 'stop-actions';
    if (stop.projectAvailable) {
      const edit = document.createElement('a'); edit.href = `/?project=${stop.projectId}`; edit.textContent = 'Edit project';
      const sync = document.createElement('button'); sync.textContent = 'Update from project'; sync.disabled = loading || syncing;
      sync.setAttribute('aria-label', `Update ${stop.name} from project`);
      sync.onclick = () => syncStop({projectId:stop.projectId, stopId:stop.id});
      actions.append(edit, sync);
    } else if (!stop.projectId) {
      const link = document.createElement('button'); link.textContent = 'Link a project';
      link.onclick = () => { $('#link-stop').value = stop.id; $('#publish-status').textContent = `Choose the project for ${stop.name}, then add / update. Its current snapshot stays playable until the update succeeds.`; $('#player-project').focus(); };
      actions.append(link);
    }
    card.append(toggle, level, review, connection, actions); cards.set(stop.id, card); $('#stops').append(card);
  }
}
function projectOptions() {
  const selected = $('#player-project').value;
  $('#player-project').replaceChildren(new Option(`Current editor: ${catalog.currentProject.name}`, 'current'));
  for (const project of catalog.projects) $('#player-project').add(new Option(project.name, project.id));
  if ([...$('#player-project').options].some(o=>o.value===selected)) $('#player-project').value = selected;
}
async function loadLibrary() {
  catalog = await request('/api/player');
  const session = await request('/api/samples'); projectToken = session.token;
  projectOptions(); buildStops();
}
async function startAudio(rebuild = false) {
  if (loading) return false;
  if (player && !rebuild) { await context.resume(); status('Audio ready. Turn on a stop and play.'); return true; }
  loading = true; $('#audio-start').disabled = true;
  const previous = player;
  let candidate;
  try {
    context ||= new AudioContext({latencyHint:'interactive'});
    await context.resume(); panic();
    candidate = new OrganPlayer(context, update);
    let total = catalog.stops.reduce((n, stop) => n + stop.samples.length, 0), count = 0, bytes = 0;
    for (const stop of catalog.stops) {
      const cached = previous?.stops.get(stop.id);
      const reusable = cached && (cached.revision || cached.id) === (stop.revision || stop.id);
      const decoded = [];
      for (const sample of stop.samples) {
        let buffer = reusable ? cached.samples.find(s=>s.id===sample.id)?.buffer : null;
        if (!buffer) {
          status(`Loading pipes... ${count + 1} / ${total}`);
          const query = stop.revision ? `?revision=${stop.revision}` : '';
          const response = await fetch(`/api/player/audio/${stop.id}/${sample.id}${query}`);
          if (!response.ok) throw new Error(`Could not load ${stop.name}: ${sample.name}. Refresh the instrument to retry.`);
          buffer = await context.decodeAudioData(await response.arrayBuffer());
          smoothLoop(buffer, sample);
        }
        bytes += buffer.length * buffer.numberOfChannels * 4;
        if (bytes > 512 * 1024 * 1024) throw new Error('This instrument exceeds the 512 MB player memory limit.');
        decoded.push({...sample, buffer}); count++;
      }
      candidate.addStop(stop, decoded);
      candidate.stops.get(stop.id).gain.gain.value = (preferences.stops[stop.id]?.level ?? 70) / 100;
      candidate.stops.get(stop.id).enabled = !!preferences.stops[stop.id]?.enabled;
    }
    candidate.master.gain.value = Number($('#volume').value) / 100;
    candidate.release = Number($('#release').value) / 1000;
    candidate.extension = Number($('#extension').value);
    previous?.destroy(); player = candidate;
    context.onstatechange = () => { if (context.state !== 'running') { panic(); status('Audio paused. Click Start audio to resume.'); } };
    status(total ? `Ready - ${total} pipes loaded. ${rebuild ? 'Updated stops are ready; your registration is preserved.' : 'Turn on a stop and play.'}` : 'Choose a register project below to begin.');
    update(); return true;
  } catch (error) {
    candidate?.destroy();
    if (!previous && context) { context.onstatechange = null; await context.close(); context = null; }
    status(error.message, true); return false;
  } finally { loading = false; $('#audio-start').disabled = false; }
}
async function refreshLibrary() {
  if (!catalog) return;
  if (loading || refreshing || syncing) { pendingRefresh = true; return; }
  refreshing = true; $('#refresh-library').disabled = true;
  const previousCatalog = catalog;
  try {
    if (player) remember();
    const next = await request('/api/player');
    const signature = c => JSON.stringify(c?.stops.map(s=>[s.id,s.revision || s.id]));
    const changed = signature(next) !== signature(catalog);
    catalog = next;
    if (changed && player && !await startAudio(true)) { catalog = previousCatalog; return; }
    projectOptions(); buildStops(); update();
    if (!changed) status(player ? 'Instrument is current. Update a stop from its project to hear newer edits.' : 'Library refreshed. Start audio to play.');
  } catch (error) { catalog = previousCatalog; status(error.message, true); }
  finally { refreshing = false; $('#refresh-library').disabled = false; }
}
async function syncStop(body) {
  if (loading || refreshing || syncing) return;
  syncing = true; $('#publish-form button[type=submit]').disabled = true;
  try {
    projectToken = (await request('/api/samples')).token;
    const result = await request('/api/player/sync', body);
    $('#publish-status').textContent = `${result.stop.name}: ${result.stop.samples.length} pipes updated${result.stop.skipped.length ? `; ${result.stop.skipped.length} notes skipped (missing key or valid loop)` : ''}.`;
    $('#link-stop').value = '';
    syncing = false; await refreshLibrary();
  } catch (error) { $('#publish-status').textContent = error.message; }
  finally { syncing = false; $('#publish-form button[type=submit]').disabled = false; }
}
function panic() { pointers.clear(); typed.clear(); player?.allOff(); }
function wireMidi() {
  panic();
  const selected = $('#midi-input').value, inputs = [...midiAccess.inputs.values()].filter(i => i.state === 'connected');
  $('#midi-input').replaceChildren(new Option('All connected inputs', 'all'));
  for (const input of inputs) $('#midi-input').add(new Option(input.name || 'MIDI keyboard', input.id));
  $('#midi-input').value = inputs.some(i => i.id === selected) ? selected : 'all';
  for (const input of midiAccess.inputs.values()) {
    input.onmidimessage = (event) => {
      if ($('#midi-input').value !== 'all' && $('#midi-input').value !== input.id) return;
      if (loading || refreshing || !player || context.state !== 'running') { status('Click Start audio before playing MIDI.'); return; }
      player.midi(event.data, input.id);
    };
  }
  $('#midi-status').textContent = inputs.length ? `${inputs.length} MIDI input${inputs.length === 1 ? '' : 's'} connected` : 'MIDI enabled · plug in your keyboard';
}
$('#midi-connect').onclick = async () => {
  try {
    if (!navigator.requestMIDIAccess) throw new Error('Web MIDI is unavailable. Try Chrome or Edge on localhost; the on-screen keyboard still works.');
    midiAccess ||= await navigator.requestMIDIAccess({sysex: false});
    midiAccess.onstatechange = wireMidi; wireMidi();
  } catch (error) { $('#midi-status').textContent = `MIDI unavailable: ${error.message}`; }
};
$('#midi-input').onchange = () => panic();
$('#audio-start').onclick = () => startAudio().catch(error => status(error.message, true));
$('#panic').onclick = panic;
$('#volume').oninput = () => { $('#volume-value').textContent = `${$('#volume').value}%`; player?.master.gain.setTargetAtTime(Number($('#volume').value) / 100, context.currentTime, .02); remember(); };
$('#release').oninput = () => { const value = Number($('#release').value) / 1000; $('#release-value').textContent = `${value.toFixed(2)} s`; if (player) player.release = value; remember(); };
$('#extension').onchange = () => { panic(); if (player) player.extension = Number($('#extension').value); update(); remember(); };
function typingOctave(step) { panic(); typingBase = Math.max(24, Math.min(84, typingBase + step)); $('#octave-label').textContent = `Typing: ${noteName(typingBase)}–${noteName(typingBase + 16)}`; }
$('#octave-down').onclick = () => typingOctave(-12);
$('#octave-up').onclick = () => typingOctave(12);
// Five octaves, low C through high C. Gray keys have no current registration.
const whiteNotes = Array.from({length: 61}, (_, i) => i + 36).filter(n => ![1,3,6,8,10].includes(n % 12));
for (let note = 36; note <= 96; note++) {
  const key = document.createElement('button'), black = [1,3,6,8,10].includes(note % 12);
  key.className = `key ${black ? 'black' : 'white'}`; key.dataset.note = note;
  key.setAttribute('aria-label', `${noteName(note)} · MIDI ${note}`); key.setAttribute('aria-pressed', 'false');
  const index = whiteNotes.filter(n => n < note).length;
  key.style.left = `${(index - (black ? .31 : 0)) * 100 / whiteNotes.length}%`;
  key.style.width = `${(black ? .62 : 1) * 100 / whiteNotes.length}%`;
  const label = document.createElement('span'); label.textContent = note % 12 === 0 ? noteName(note) : ''; key.append(label);
  key.onpointerdown = (event) => {
    event.preventDefault();
    if (loading || refreshing || !player || context.state !== 'running') { status('Click Start audio, then turn on a stop.'); return; }
    key.setPointerCapture(event.pointerId); pointers.set(event.pointerId, note);
    player.noteOn(note, 100, `pointer-${event.pointerId}`);
  };
  const release = (event) => { const n = pointers.get(event.pointerId); if (n !== undefined) player?.noteOff(n, `pointer-${event.pointerId}`); pointers.delete(event.pointerId); };
  key.onpointerup = key.onpointercancel = key.onlostpointercapture = release;
  key.onkeydown = (event) => {
    if (!['Enter', 'Space'].includes(event.code) || event.repeat) return;
    event.preventDefault(); if (!loading && !refreshing && player && context.state === 'running') player.noteOn(note, 100, 'accessible');
  };
  key.onkeyup = (event) => { if (['Enter', 'Space'].includes(event.code)) { event.preventDefault(); player?.noteOff(note, 'accessible'); } };
  key.onblur = () => player?.noteOff(note, 'accessible');
  $('#keyboard').append(key);
}
window.addEventListener('keydown', (event) => {
  if (event.code === 'Escape') { panic(); return; }
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || /INPUT|SELECT|TEXTAREA/.test(event.target.tagName) || event.target.isContentEditable) return;
  const index = typingCodes.indexOf(event.code);
  if (index < 0) return;
  event.preventDefault();
  if (loading || refreshing || !player || context.state !== 'running') { status('Click Start audio, then turn on a stop.'); return; }
  typed.set(event.code, typingBase + index); player.noteOn(typingBase + index, 100, 'typing');
});
window.addEventListener('keyup', (event) => { const note = typed.get(event.code); if (note !== undefined) { player?.noteOff(note, 'typing'); typed.delete(event.code); } });
window.addEventListener('blur', panic);
document.addEventListener('visibilitychange', () => { if (document.hidden) panic(); });
window.addEventListener('pagehide', panic);
const linkStop = document.createElement('input'); linkStop.type = 'hidden'; linkStop.id = 'link-stop'; $('#publish-form').append(linkStop);
$('#publish-form').onsubmit = async (event) => {
  event.preventDefault();
  const selected = $('#player-project').value;
  const body = selected === 'current' ? {current:true} : {projectId:selected};
  if ($('#link-stop').value) body.stopId = $('#link-stop').value;
  await syncStop(body);
};
$('#refresh-library').onclick = () => refreshLibrary();
const updates = typeof BroadcastChannel === 'function' ? new BroadcastChannel('loop-instrument') : null;
updates?.addEventListener('message', event => { if (event.data?.type === 'updated') { pendingRefresh = true; if (!document.hidden) refreshLibrary(); } });
window.addEventListener('focus', () => refreshLibrary());
window.addEventListener('pagehide', remember);
setInterval(() => { if (pendingRefresh && !document.hidden && !loading && !refreshing && !syncing) { pendingRefresh = false; refreshLibrary(); } }, 1000);
loadLibrary().catch(error => { status(error.message, true); $('#stops').textContent = 'The stop library could not load. Check the local server, then reload this page.'; });
