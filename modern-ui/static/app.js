'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = (text) => String(text).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const markerKeys = ['attack', 'loopStart', 'loopEnd', 'release', 'end'];
let samples = [], selected = new Set(), activeId = null, filter = 'all', busy = false;
let projectState = null, workspaceToken = null;
let stage = 'import', sourceId = null;
const sourceIds = () => new Set([...samples.filter(s => s.kind === 'recording').map(s => s.id), ...samples.map(s => s.source?.id).filter(Boolean), ...Object.keys(projectState?.drafts || {})]);
const recordingSamples = () => samples.filter(s => sourceIds().has(s.id));
const noteSamples = () => samples.filter(s => !sourceIds().has(s.id));
let settings = {minimumLoop: .4, threshold: -55};
let zoom = 1, viewStart = 0, mode = 'full', position = 0, dragging = null;
let audioContext, gain, source, audioBuffer, bufferId, playbackStart = 0, playbackOffset = 0, audioGeneration = 0;
let toastTimer, animationFrame, undoHistory = new Map();
let loopOptions = [], loopOptionsKey = null;
const loopSearchKey = (sample) => sample ? JSON.stringify([sample.id, sample.markers.attack, sample.markers.release, sample.markers.end, settings.minimumLoop]) : null;
const active = () => samples.find((sample) => sample.id === activeId);
const noteName = (note) => note == null ? '—' : ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][note % 12] + (Math.floor(note / 12) - 1);
const time = (seconds, detailed = false) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(detailed ? 3 : 0).padStart(detailed ? 6 : 2, '0')}`;
const visible = () => noteSamples().filter((s) => (filter === 'all' || (filter === 'approved' ? s.reviewed : !s.reviewed)) && s.name.toLowerCase().includes($('#search').value.toLowerCase()));
const targets = () => selected.size ? noteSamples().filter((s) => selected.has(s.id)) : visible();

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 9000 : 5000);
}

async function request(path, body, binary = false) {
  let response;
  try {
    response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Loop-Workspace': '1', ...(workspaceToken ? {'X-Loop-Project': workspaceToken} : {})}, body: JSON.stringify(body)});
  } catch {
    $('#save-state').innerHTML = 'Connection lost';
    throw new Error('The local server is unavailable. Start server.py, then refresh this page.');
  }
  if (!response.ok) {
    let message = 'The operation could not complete.';
    try { message = (await response.json()).error || message; } catch { /* Non-JSON server error. */ }
    throw new Error(message);
  }
  $('#save-state').innerHTML = '<span class="local-dot"></span>Autosaved locally';
  return binary ? response.arrayBuffer() : response.json();
}

function updateRecord(record) {
  const index = samples.findIndex((s) => s.id === record.id);
  if (index < 0) samples.push(record); else samples[index] = record;
}

function setBusy(value, message = '') {
  busy = value;
  for (const id of ['import-top','detect-batch','settings-open','remove-samples','demo-load','guide-demo','step-import','step-detect','project-new','project-open','project-save','project-name']) $("#" + id).disabled = value;
  $('#progress-track').hidden = !value;
  if (value) {
    stopPlayback();
    $('#save-state').textContent = message || 'Working locally…';
    $('#progress-bar').style.width = '0%';
  } else $('#save-state').innerHTML = '<span class="local-dot"></span>Autosaved locally';
  render();
}

function status(sample) {
  return sample.reviewed ? ['ready', sample.testApproved ? 'Test approved' : 'Approved'] : sample.analyzed && sample.markers.loopStart == null ? ['pending', 'No loop found'] : sample.analyzed || sample.manual ? ['', 'Needs review'] : ['pending', 'Not analyzed'];
}

function render() {
  const notes = noteSamples();
  selected = new Set([...selected].filter(id => notes.some(s => s.id === id)));
  if (!notes.some(s => s.id === activeId)) activeId = notes[0]?.id || null;
  const approved = notes.filter((s) => s.reviewed).length;
  $('#count-all').textContent = notes.length;
  $('#count-review').textContent = notes.length - approved;
  $('#count-approved').textContent = approved;
  $('#batch-count').textContent = `${notes.length} sample${notes.length === 1 ? '' : 's'}`;
  $('#batch-title').textContent = notes.length && notes.every((s) => s.demo) ? 'Example rank' : 'Note samples';
  $('#selection-summary').textContent = selected.size ? `${selected.size} selected · ${approved} approved in batch` : `${notes.length} samples · ${approved} approved`;
  $('#batch-meta').textContent = notes.some((s) => s.demo) ? 'Example audio included' : notes.length ? `${(notes.reduce((n, s) => n + s.bytes, 0) / 1048576).toFixed(1)} MB · saved locally` : 'PCM & float WAV supported';
  $('#detect-batch span').textContent = `Detect loops (${targets().filter(s => !s.reviewed && !s.manual).length})`;
  $('#detect-batch').disabled = busy || !notes.length;
  $('#approve-all').textContent = `Approve ${selected.size ? 'selected' : 'visible'} notes (${targets().length})`;
  $('#approve-all').disabled = busy || !targets().some(s => !s.reviewed || s.testApproved);
  $('#remove-samples').disabled = busy || !selected.size;
  $('#empty-state').hidden = !!notes.length;
  const rows = visible();
  $('#sample-list').innerHTML = rows.map((s) => {
    const [kind, label] = status(s);
    return `<tr class="sample-row ${s.id === activeId ? 'selected' : ''}" data-id="${s.id}" aria-selected="${s.id === activeId}"><td class="check-cell"><input type="checkbox" data-select="${s.id}" aria-label="Select ${escapeHTML(s.name)}" ${selected.has(s.id) ? 'checked' : ''} ${busy ? 'disabled' : ''}></td><td class="filename"><div class="filename-inner">${icon('wave')}<span class="sample-title" title="${escapeHTML(s.name)}">${escapeHTML(s.name)}</span></div></td><td>${noteName(s.note)}</td><td>${s.duration.toFixed(2)} s</td><td><span class="status ${kind}">${label}</span></td></tr>`;
  }).join('') || (notes.length ? '<tr class="no-results"><td colspan="5">No samples match this view.</td></tr>' : '');
  $('#select-all').checked = !!rows.length && rows.every((s) => selected.has(s.id));
  $('#select-all').indeterminate = rows.some((s) => selected.has(s.id)) && !$('#select-all').checked;
  $('#select-all').disabled = busy || !rows.length;
  $$('[data-filter]').forEach((b) => b.classList.toggle('active', b.dataset.filter === filter));
  const sample = active();
  $('#split-open').disabled = busy || !recordingSamples().length;
  $('#wave-empty').hidden = !!sample;
  $('#editor-title').textContent = sample?.name || 'Your sound, up close.';
  $('#audio-meta').textContent = sample ? `${(sample.rate / 1000).toFixed(1)} kHz  ·  ${sample.bits}-bit  ·  ${sample.channels === 2 ? 'Stereo' : 'Mono'}  ·  ${sample.demo ? 'Generated example audio' : 'Original audio preserved'}` : 'Choose a sample to start listening';
  $('#sample-position').textContent = sample ? `${notes.indexOf(sample) + 1} / ${notes.length}` : '— / —';
  $('#total-time').textContent = ` / ${time(sample?.duration || 0)}`;
  $('#previous').disabled = busy || !sample || notes.indexOf(sample) === 0;
  $('#next').disabled = busy || !sample || notes.indexOf(sample) === notes.length - 1;
  for (const id of ['play','stop','zoom-in','zoom-out','zoom-reset','detect-single','root-note']) $('#' + id).disabled = busy || !sample;
  $$('[data-mode]').forEach((b) => b.disabled = busy || !sample || (b.dataset.mode === 'loop' && sample.markers.loopStart == null));
  $('#approve').disabled = busy || !sample || sample.markers.loopStart == null || sample.note == null || (sample.reviewed && !sample.testApproved);
  $('#approve').innerHTML = icon('check') + (sample?.testApproved ? 'Approve after listening' : sample?.reviewed ? 'Sample approved' : 'Approve sample');
  $('#undo').disabled = busy || !sample || !undoHistory.get(sample.id)?.length;
  const [kind, label] = sample ? status(sample) : ['pending', 'Awaiting a sample'];
  $('#inspector-status').className = `status ${kind}`;
  $('#inspector-status').textContent = label;
  $('#match-score').textContent = sample?.quality != null ? `${sample.quality}% seam match` : '';
  markerKeys.forEach((key) => {
    const input = $('#marker-' + key);
    input.disabled = busy || !sample;
    input.value = sample?.markers[key] != null ? (sample.markers[key] / sample.rate * 1000).toFixed(3) : '';
    input.placeholder = '—';
    input.max = sample ? (sample.frames / sample.rate * 1000).toFixed(3) : '';
  });
  $('#root-note').value = sample?.note ?? '';
  $('#note-name').textContent = noteName(sample?.note);
  $('#pitch-estimate').textContent = sample?.analyzed
    ? sample.estimatedNote == null ? 'Sounding pitch: uncertain. Listen and verify the keyboard key.'
      : `Estimated sound: ${noteName(sample.estimatedNote)} · MIDI ${sample.estimatedNote} · confidence ${sample.pitchConfidence ?? '—'}%. Stop footage can differ from the keyboard key.`
    : 'Sounding pitch is estimated during detection.';
  $('#loop-duration').textContent = sample?.markers.loopStart != null ? `${((sample.markers.loopEnd - sample.markers.loopStart) / sample.rate).toFixed(3)} s of sustain` : !sample ? 'Select a note to prepare its loop.' : sample.analyzed ? 'No loop found. Check the range or minimum loop length.' : sample.manual ? 'No loop set. Add points or run detection.' : 'Not analyzed. Run Detect loops to find a sustain loop.';
  $('#detect-single').textContent = sample?.analyzed || sample?.manual ? 'Detect this sample again' : 'Detect loops for this note';
  const messages = sample?.reviewed ? [sample.testApproved ? 'Bulk approved for testing. Listening review is still needed. Any edit removes approval.' : 'Reviewed and ready to export. Any marker edit will return this sample to review.'] : sample?.warnings?.length ? sample.warnings : ['Automatic detection gets you close. Listening gets you there.'];
  $('#review-note').innerHTML = icon('review') + '<ul>' + messages.map((message) => `<li>${escapeHTML(message)}</li>`).join('') + '</ul>';
  if (loopOptionsKey !== loopSearchKey(sample)) {
    loopOptions = []; loopOptionsKey = loopSearchKey(sample);
    $('#loop-choice').innerHTML = '<option>No alternatives loaded</option>';
    $('#loop-options-status').textContent = 'Compare different seams. Undo restores the previous loop.';
  }
  $('#loop-find').disabled = busy || !sample;
  $('#loop-choice').disabled = $('#loop-try').disabled = busy || !loopOptions.length;
  drawWave();
  if (typeof renderFlow === 'function') renderFlow();
  if (typeof scheduleProjectViewSave === 'function') scheduleProjectViewSave();
}

function choose(identity) {
  if (busy || identity === activeId || sourceIds().has(identity)) return;
  stopPlayback();
  activeId = identity;
  audioBuffer = null;
  bufferId = null;
  position = 0;
  zoom = 1;
  viewStart = 0;
  $('#zoom-reset').textContent = '1×';
  render();
}

function moveSample(direction) {
  const list = noteSamples(), index = list.findIndex(s => s.id === activeId) + direction;
  if (index >= 0 && index < list.length) choose(list[index].id);
}

async function importFiles(files, kind = $('#import-kind').value) {
  if (busy) return;
  const all = [...files];
  const wavs = all.filter((f) => f.name.toLowerCase().endsWith('.wav'));
  if (!wavs.length) return toast('Choose mono or stereo WAV files to import.', true);
  setBusy(true, 'Importing local copies…');
  let imported = 0, errors = [];
  for (const [index, file] of wavs.entries()) {
    try {
      if (file.size > 300 * 1024 * 1024) throw new Error('exceeds 300 MB');
      const response = await fetch('/api/import?kind=' + kind + '&name=' + encodeURIComponent(file.name), {method: 'POST', headers: {'X-Loop-Workspace': '1', ...(workspaceToken ? {'X-Loop-Project': workspaceToken} : {})}, body: file});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      updateRecord(result);
      if (!imported) { if (kind === 'recording') sourceId = result.id; else activeId = result.id; }
      imported++;
    } catch (error) { errors.push(`${file.name}: ${error.message}`); }
    $('#progress-bar').style.width = `${(index + 1) / wavs.length * 100}%`;
  }
  selected.clear(); filter = 'all'; $('#search').value = ''; position = 0; zoom = 1; viewStart = 0; bufferId = null;
  setBusy(false);
  const skipped = all.length - wavs.length;
  toast(`${imported} sample${imported === 1 ? '' : 's'} imported.${skipped ? ` ${skipped} non-WAV files skipped.` : ''}${errors.length ? ` ${errors.length} failed: ${errors[0]}` : ' Ready to detect.'}`, !!errors.length);
  $('#file-input').value = '';
  if (imported && typeof setStage === 'function') await setStage(kind === 'recording' ? 'split' : 'loops');
}

async function loadDemo() {
  if (busy) return;
  $('#guide-dialog').close();
  setBusy(true, 'Generating example rank…');
  try {
    samples = (await request('/api/demo', {})).samples;
    activeId = samples.find((s) => s.demo)?.id || samples[0]?.id;
    filter = 'all'; $('#search').value = ''; selected.clear(); zoom = 1; viewStart = 0;
    stage = 'loops';
    toast('12 generated pipe tones loaded. Try Loop, adjust a marker, then approve.');
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
}

async function detect(single = false, pendingOnly = false) {
  if (busy) return;
  const list = single ? [active()].filter(Boolean) : targets().filter((s) => !s.reviewed && !s.manual && (!pendingOnly || !s.analyzed));
  if (!list.length) return toast('Approved and manually edited samples are protected. Use “Detect this sample again” to replace their markers.');
  if (single && (active().reviewed || active().manual || undoHistory.get(activeId)?.length) && !confirm('Detect again and replace this sample’s current markers? You can undo this change.')) return;
  setBusy(true, 'Finding natural boundaries…');
  let failed = [], done = 0, loopsFound = 0;
  for (const sample of list) {
    try {
      const result = await request('/api/analyze', {id: sample.id, ...settings});
      remember(sample);
      updateRecord(result);
      if (result.markers.loopStart != null) loopsFound++;
      done++;
    } catch (error) { failed.push(`${sample.name}: ${error.message}`); }
    $('#progress-bar').style.width = `${(done + failed.length) / list.length * 100}%`;
  }
  setBusy(false);
  const summary = `Loops found in ${loopsFound} of ${done} analyzed notes.${done > loopsFound ? ` ${done - loopsFound} need loop review; check short or noisy regions.` : ' Listen and approve when ready.'}`;
  toast(failed.length ? `${summary} ${failed.length} failed: ${failed[0]}` : summary, !!failed.length);
}

async function approveAllForTesting() {
  if (busy || !targets().length) return;
  const ids = targets().map(s => s.id), testing = $('#bulk-approval-mode').value === 'test';
  setBusy(true, 'Validating and approving note samples...');
  try {
    const result = await request('/api/approve-batch', {ids, testing});
    samples = result.samples;
    $('#bulk-result').hidden = false;
    $('#bulk-result').replaceChildren();
    const summary = document.createElement('p');
    summary.textContent = `${result.approved.length} ${testing ? 'test approved' : 'approved after listening'}; ${result.skipped.length} skipped. Already approved notes were kept.`;
    $('#bulk-result').append(summary);
    if (result.skipped.length) {
      const details = document.createElement('details'), heading = document.createElement('summary'), list = document.createElement('ul');
      heading.textContent = 'Notes that need correction';
      for (const item of result.skipped) { const row = document.createElement('li'); row.textContent = `${item.name}: ${item.reason}`; list.append(row); }
      details.append(heading, list); $('#bulk-result').append(details);
    }
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
}

async function findLoopAlternatives() {
  const sample = active();
  if (!sample || busy) return;
  setBusy(true, 'Finding alternative loop seams…');
  try {
    const result = await request('/api/loop-candidates', {id: sample.id, minimumLoop: settings.minimumLoop});
    loopOptions = result.candidates; loopOptionsKey = loopSearchKey(sample);
    $('#loop-choice').innerHTML = loopOptions.map((c, i) => `<option value="${i}">${i + 1} · ${((c.loopEnd - c.loopStart) / sample.rate).toFixed(3)} s · ${c.quality}% seam</option>`).join('') || '<option>No alternatives found</option>';
    $('#loop-options-status').textContent = loopOptions.length ? 'Choose a suggestion, then Try & listen. Seam score is a guide; trust your ears.' : 'No alternative fits. Adjust attack/release bounds or lower the minimum loop length in Detection settings.';
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
}

async function tryLoopAlternative() {
  const sample = active(), candidate = loopOptions[Number($('#loop-choice').value)];
  if (busy || !sample || !candidate || loopOptionsKey !== loopSearchKey(sample)) return;
  const markers = {...sample.markers, loopStart: candidate.loopStart, loopEnd: candidate.loopEnd};
  if (await saveEdit(markers, sample.note)) {
    $('#proposal-status').textContent = 'Selected suggestion applied to the saved loop. Undo restores the previous markers.';
    mode = 'loop'; position = 0;
    $$('[data-mode]').forEach((button) => button.classList.toggle('active', button.dataset.mode === mode));
    $('#loop-options-status').textContent = 'Trying this loop. Undo restores the previous markers; approve again after listening.';
    await play();
  }
}

$('#approve-all').onclick = approveAllForTesting;
$('#loop-find').onclick = findLoopAlternatives;
$('#loop-try').onclick = tryLoopAlternative;

function remember(sample) {
  const history = undoHistory.get(sample.id) || [];
  history.push({markers: {...sample.markers}, note: sample.note});
  undoHistory.set(sample.id, history.slice(-20));
}

async function saveEdit(markers, note, review = false, rememberChange = true) {
  const sample = active();
  if (!sample || busy) return;
  setBusy(true, 'Saving markers…');
  try {
    const result = await request('/api/update', {id: sample.id, markers, note, reviewed: review});
    if (rememberChange) remember(sample);
    updateRecord(result);
    if (review) toast('Sample approved. Ready for export.');
    return true;
  } catch (error) { toast(error.message, true); return false; }
  finally { setBusy(false); }
}

async function editMarker(key, milliseconds) {
  const sample = active();
  if (!sample) return;
  if (!Number.isFinite(milliseconds)) { render(); return toast('Enter a marker time in milliseconds.', true); }
  const markers = {...sample.markers, [key]: Math.round(milliseconds / 1000 * sample.rate)};
  if (markers.loopStart == null && key === 'loopEnd') markers.loopStart = Math.max(markers.attack, markers.loopEnd - Math.round(sample.rate * .4));
  if (markers.loopEnd == null && key === 'loopStart') markers.loopEnd = Math.min(markers.release, markers.loopStart + Math.round(sample.rate * .4));
  await saveEdit(markers, sample.note);
}

function stopPlayback(reset = true) {
  audioGeneration++;
  if (source) { source.onended = null; source.stop(); source.disconnect(); source = null; }
  cancelAnimationFrame(animationFrame);
  if (reset) position = 0;
  $('#play').innerHTML = icon('play');
  $('#play').setAttribute('aria-label', 'Play sample');
  $('#play-time').textContent = time(position, true);
  drawWave();
}

async function play() {
  const sample = active();
  if (!sample || busy) return;
  if (source) return stopPlayback();
  const generation = ++audioGeneration;
  $('#play').disabled = true;
  try {
    if (!audioContext) {
      audioContext = new AudioContext(); gain = audioContext.createGain();
      gain.connect(audioContext.destination);
    }
    await audioContext.resume();
    if (bufferId !== sample.id) {
      const raw = await request('/api/audio/' + sample.id, undefined, true);
      const decoded = await audioContext.decodeAudioData(raw);
      if (generation !== audioGeneration || sample.id !== activeId) return;
      audioBuffer = decoded; bufferId = sample.id;
    }
    if (generation !== audioGeneration) return;
    const start = mode === 'loop' ? sample.markers.loopStart / sample.rate : mode === 'release' ? sample.markers.release / sample.rate : Math.max(sample.markers.attack / sample.rate, Math.min(position, (sample.markers.end - 1) / sample.rate));
    if (!Number.isFinite(start) || (mode === 'loop' && sample.markers.loopStart == null)) throw new Error('Detect or place loop markers first.');
    source = audioContext.createBufferSource(); source.buffer = audioBuffer; source.connect(gain);
    gain.gain.value = Number($('#volume').value) / 100;
    source.loop = mode === 'loop';
    if (source.loop) { source.loopStart = sample.markers.loopStart / sample.rate; source.loopEnd = sample.markers.loopEnd / sample.rate; }
    playbackStart = audioContext.currentTime; playbackOffset = start;
    source.onended = () => stopPlayback();
    if (source.loop) source.start(0, start);
    else source.start(0, start, Math.max(1 / sample.rate, sample.markers.end / sample.rate - start));
    $('#play').innerHTML = icon('stop'); $('#play').setAttribute('aria-label', 'Stop sample audition');
    tick();
  } catch (error) { toast('Playback could not start: ' + error.message, true); }
  finally { $('#play').disabled = busy || !active(); }
}

function tick() {
  if (!source || !active()) return;
  const elapsed = audioContext.currentTime - playbackStart;
  position = playbackOffset + elapsed;
  if (source.loop) position = source.loopStart + elapsed % (source.loopEnd - source.loopStart);
  $('#play-time').textContent = time(position, true);
  drawWave();
  animationFrame = requestAnimationFrame(tick);
}

function drawWave() {
  const canvas = $('#waveform');
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  if (!rect.width || !rect.height) return;
  const width = rect.width, height = rect.height;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const sample = active();
  const top = 30, bottom = height - 25, pad = 11, plotWidth = width - pad * 2;
  ctx.strokeStyle = '#2b3523'; ctx.lineWidth = .6;
  for (let i = 0; i <= 8; i++) { const x = pad + plotWidth * i / 8; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); }
  if (!sample) return;
  const span = sample.frames / zoom;
  viewStart = Math.max(0, Math.min(viewStart, sample.frames - span));
  const px = (frame) => pad + (frame - viewStart) / span * plotWidth;
  const m = dragging?.preview || sample.markers;
  const regions = [[m.attack, m.loopStart, '#91b7b711'], [m.loopStart, m.loopEnd, '#a8c7861b'], [m.release, m.end, '#b69aca13']];
  for (const [start, end, color] of regions) {
    if (start == null || end == null) continue;
    ctx.fillStyle = color; const a = Math.max(pad, px(start)), b = Math.min(width - pad, px(end));
    if (b > a) ctx.fillRect(a, top, b - a, bottom - top);
  }
  const channelHeight = (bottom - top) / sample.channels;
  sample.peaks.forEach((channel, index) => {
    const middle = top + channelHeight * (index + .5);
    ctx.strokeStyle = '#3f4b32'; ctx.beginPath(); ctx.moveTo(pad, middle); ctx.lineTo(width - pad, middle); ctx.stroke();
    for (let x = 0; x < plotWidth; x++) {
      const frame = viewStart + x / plotWidth * span;
      const a = Math.max(0, Math.floor(frame / sample.frames * channel.length));
      const b = Math.min(channel.length, Math.max(a + 1, Math.ceil((frame + span / plotWidth) / sample.frames * channel.length)));
      let low = 0, high = 0;
      for (let i = a; i < b; i++) { low = Math.min(low, channel[i][0]); high = Math.max(high, channel[i][1]); }
      ctx.strokeStyle = frame >= m.loopStart && frame <= m.loopEnd && m.loopStart != null ? '#b5cd8f' : frame >= m.release ? '#ae97bf' : '#849c71';
      ctx.beginPath(); ctx.moveTo(pad + x, middle - high * channelHeight * .86); ctx.lineTo(pad + x, middle - low * channelHeight * .86); ctx.stroke();
    }
    ctx.fillStyle = '#667856'; ctx.font = '8px Segoe UI'; ctx.fillText(sample.channels === 2 ? (index === 0 ? 'L' : 'R') : 'M', 3, middle - 5);
  });
  ctx.font = '8px Segoe UI'; ctx.fillStyle = '#728261';
  for (let i = 0; i <= 6; i++) {
    const x = pad + plotWidth * i / 6;
    const label = ((viewStart + span * i / 6) / sample.rate).toFixed(zoom > 4 ? 2 : 1) + 's';
    ctx.textAlign = i === 0 ? 'left' : i === 6 ? 'right' : 'center'; ctx.fillText(label, x, height - 8);
  }
  const names = {attack:'A', loopStart:'IN', loopEnd:'OUT', release:'R', end:'END'};
  for (const key of markerKeys) {
    if (m[key] == null) continue;
    const x = px(m[key]); if (x < pad - 1 || x > width - pad + 1) continue;
    const color = key === 'attack' ? '#91b7b7' : key.startsWith('loop') ? '#b6d08e' : '#bda1cf';
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(key === 'end' ? [3, 3] : []);
    ctx.beginPath(); ctx.moveTo(x, top - 6); ctx.lineTo(x, bottom); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = color; const textWidth = key === 'loopEnd' || key === 'end' ? 25 : 20;
    const left = Math.max(1, Math.min(width - textWidth - 1, x - textWidth / 2));
    ctx.fillRect(left, 9, textWidth, 14); ctx.fillStyle = '#202a18'; ctx.textAlign = 'center'; ctx.font = 'bold 7px Segoe UI'; ctx.fillText(names[key], left + textWidth / 2, 19);
  }
  if (position > 0) {
    const x = px(position * sample.rate);
    if (x >= pad && x <= width - pad) { ctx.strokeStyle = '#f1efde'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); }
  }
}

function zoomWave(factor) {
  const sample = active(); if (!sample) return;
  const center = position > 0 ? position * sample.rate : (sample.markers.loopStart != null ? (sample.markers.loopStart + sample.markers.loopEnd) / 2 : sample.frames / 2);
  zoom = factor === 0 ? 1 : Math.max(1, Math.min(32, zoom * factor));
  viewStart = Math.max(0, Math.min(sample.frames - sample.frames / zoom, center - sample.frames / zoom / 2));
  $('#zoom-reset').textContent = zoom + '×'; drawWave();
}

function exportCandidates() { return targets().filter((s) => s.reviewed); }
function openExport() {
  if (busy) return toast('Let the current operation finish first.');
  const list = exportCandidates();
  $('#export-count').textContent = `${list.length} approved sample${list.length === 1 ? '' : 's'}`;
  $('#export-selection').textContent = selected.size ? `From ${selected.size} selected · others skipped` : 'From the whole batch';
  $('#export-download').disabled = !list.length;
  if (typeof setStage === 'function') setStage('export');
}

async function downloadExport() {
  const ids = exportCandidates().map((s) => s.id);
  if (!ids.length || busy) return;
  $('#export-download').disabled = true;
  $('#export-download').textContent = 'Preparing ZIP…';
  setBusy(true, 'Preparing your export…');
  try {
    const data = await request('/api/export', {ids, mode: $('input[name="export-mode"]:checked').value, trim: $('#export-trim').checked}, true);
    const url = URL.createObjectURL(new Blob([data], {type:'application/zip'}));
    const link = document.createElement('a'); link.href = url; link.download = 'LoopAuditioneer-prepared.zip'; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast(`${ids.length} prepared sample${ids.length === 1 ? '' : 's'} exported. Your originals are intact.`);
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); $('#export-download').disabled = false; $('#export-download').innerHTML = icon('export') + 'Export ZIP'; }
}

$('#import-top').onclick = $('#step-import').onclick = () => { if (!busy) $('#file-input').click(); };
$('#file-input').onchange = (event) => importFiles(event.target.files);
$('#demo-load').onclick = $('#guide-demo').onclick = loadDemo;
$('#detect-batch').onclick = $('#step-detect').onclick = () => detect();
$('#detect-single').onclick = () => detect(true);
$('#search').oninput = render;
$$('[data-filter]').forEach((button) => button.onclick = () => { filter = button.dataset.filter; render(); });
$('#sample-list').onclick = (event) => {
  if (busy) return;
  const checkbox = event.target.closest('[data-select]');
  if (checkbox) { if (checkbox.checked) selected.add(checkbox.dataset.select); else selected.delete(checkbox.dataset.select); render(); return; }
  const row = event.target.closest('[data-id]'); if (row) choose(row.dataset.id);
};
$('#select-all').onchange = (event) => { visible().forEach((s) => event.target.checked ? selected.add(s.id) : selected.delete(s.id)); render(); };
$('#previous').onclick = () => moveSample(-1); $('#next').onclick = () => moveSample(1);
$('#approve').onclick = () => { const s = active(); if (s) saveEdit({...s.markers}, s.note, true); };
markerKeys.forEach((key) => $('#marker-' + key).onchange = (event) => editMarker(key, event.target.value === '' ? NaN : Number(event.target.value)));
$('#root-note').onchange = (event) => { const s = active(); if (s) saveEdit({...s.markers}, event.target.value === '' ? null : Number(event.target.value)); };
$('#undo').onclick = async () => {
  const s = active(), history = s && undoHistory.get(s.id); if (!history?.length) return;
  const previous = history[history.length - 1];
  if (await saveEdit(previous.markers, previous.note, false, false)) { history.pop(); render(); $('#proposal-status').textContent = 'Previous markers restored. Proposed points remain separate from the saved loop.'; $('#loop-options-status').textContent = 'Previous markers restored. Search again or try another suggestion.'; toast('Previous markers restored.'); }
};
$('#play').onclick = play; $('#stop').onclick = () => stopPlayback();
$('#volume').oninput = (event) => { if (gain) gain.gain.value = Number(event.target.value) / 100; };
$$('[data-mode]').forEach((button) => button.onclick = () => {
  stopPlayback(); mode = button.dataset.mode;
  $$('[data-mode]').forEach((b) => b.classList.toggle('active', b === button));
  if (mode === 'release' && active()) position = active().markers.release / active().rate;
  if (mode === 'loop' && active()) position = active().markers.loopStart / active().rate;
  $('#play-time').textContent = time(position, true); drawWave();
});
$('#zoom-in').onclick = () => zoomWave(2); $('#zoom-out').onclick = () => zoomWave(.5); $('#zoom-reset').onclick = () => zoomWave(0);
$('#export-side').onclick = $('#step-export').onclick = openExport;
$('#export-download').onclick = downloadExport;
$$('[data-close]').forEach((button) => button.onclick = () => $('#' + button.dataset.close).close());
$('#guide-open').onclick = () => $('#guide-dialog').showModal();
$('#settings-open').onclick = () => { $('#minimum-loop').value = settings.minimumLoop * 1000; $('#threshold').value = settings.threshold; $('#settings-dialog').showModal(); };
$('#settings-reset').onclick = () => { $('#minimum-loop').value = 400; $('#threshold').value = -55; };
$('#settings-save').onclick = () => {
  if (!$('#minimum-loop').reportValidity() || !$('#threshold').reportValidity() || !$('#minimum-loop').value || !$('#threshold').value) return;
  settings = {minimumLoop: Number($('#minimum-loop').value) / 1000, threshold: Number($('#threshold').value)};
  $('#settings-dialog').close(); toast('Detection settings updated for this session.');
};
$('#remove-samples').onclick = async () => {
  if (busy || !selected.size || !confirm(`Remove ${selected.size} selected sample(s) and their edits from this workspace? Your source files stay untouched.`)) return;
  setBusy(true, 'Removing local copies…');
  try {
    samples = (await request('/api/remove', {ids:[...selected]})).samples;
    for (const id of selected) undoHistory.delete(id);
    selected.clear(); if (!active()) activeId = samples[0]?.id || null;
    bufferId = null; position = 0; zoom = 1; viewStart = 0;
    toast('Selected local copies removed.');
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
};

let dragDepth = 0;
document.addEventListener('dragenter', (event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); dragDepth++; if (!busy) $('#drop-overlay').hidden = false; } });
document.addEventListener('dragover', (event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop-overlay').hidden = true; } });
document.addEventListener('drop', (event) => { event.preventDefault(); dragDepth = 0; $('#drop-overlay').hidden = true; if (event.dataTransfer.files.length) importFiles(event.dataTransfer.files); });
document.addEventListener('keydown', (event) => {
  if (stage !== 'loops' || busy || event.target.closest('input,textarea,select,button') || $('dialog[open]')) return;
  if (event.code === 'Space') { event.preventDefault(); play(); }
  if (event.key === 'ArrowLeft') { event.preventDefault(); moveSample(-1); }
  if (event.key === 'ArrowRight') { event.preventDefault(); moveSample(1); }
});

const canvas = $('#waveform');
canvas.addEventListener('pointerdown', (event) => {
  const s = active(); if (!s || busy) return;
  const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, width = rect.width - 22, span = s.frames / zoom;
  const nearest = markerKeys.filter((key) => s.markers[key] != null).map((key) => ({key, distance: Math.abs(11 + (s.markers[key] - viewStart) / span * width - x)})).sort((a,b) => a.distance - b.distance)[0];
  if (nearest?.distance < 11) {
    stopPlayback(); dragging = {key:nearest.key, preview:{...s.markers}}; canvas.setPointerCapture(event.pointerId); canvas.style.cursor = 'ew-resize';
  } else {
    stopPlayback(false); position = Math.max(0, Math.min(s.duration, (viewStart + (x - 11) / width * span) / s.rate));
    $('#play-time').textContent = time(position, true); drawWave();
  }
});
canvas.addEventListener('pointermove', (event) => {
  const s = active(); if (!s || busy) return;
  const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left;
  if (!dragging) {
    canvas.style.cursor = markerKeys.some((key) => s.markers[key] != null && Math.abs(11 + (s.markers[key] - viewStart) / (s.frames / zoom) * (rect.width - 22) - x) < 11) ? 'ew-resize' : 'crosshair';
    return;
  }
  const m = dragging.preview, key = dragging.key;
  let frame = Math.round(viewStart + (x - 11) / (rect.width - 22) * (s.frames / zoom));
  const bounds = {attack:[0, m.loopStart ?? m.release], loopStart:[m.attack, (m.loopEnd ?? m.release) - 1], loopEnd:[(m.loopStart ?? m.attack) + 1, m.release], release:[m.loopEnd ?? m.attack, m.end - 1], end:[m.release + 1, s.frames]};
  m[key] = Math.max(bounds[key][0], Math.min(bounds[key][1], frame));
  $('#marker-' + key).value = (m[key] / s.rate * 1000).toFixed(3); drawWave();
});
canvas.addEventListener('pointerup', async () => {
  if (!dragging) return;
  const edit = dragging; dragging = null; canvas.style.cursor = 'crosshair';
  if (edit.preview[edit.key] !== active().markers[edit.key]) await saveEdit(edit.preview, active().note);
  else render();
});
canvas.addEventListener('pointercancel', () => { dragging = null; canvas.style.cursor = 'crosshair'; render(); });
new ResizeObserver(drawWave).observe(canvas);
window.addEventListener('beforeunload', (event) => { if (busy) { event.preventDefault(); event.returnValue = ''; } });

(async () => {
  if (document.readyState !== 'complete') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
  try { const data = await request('/api/samples'); samples = data.samples; projectState = data.project || null; workspaceToken = data.token || null; activeId = samples[0]?.id || null; if (typeof restoreProjectUI === 'function') restoreProjectUI(); render(); if (typeof splitIsVisible === 'function' && splitIsVisible()) prepareSplitter(); await openRequestedProject(); }
  catch (error) { toast(error.message, true); }
})();
