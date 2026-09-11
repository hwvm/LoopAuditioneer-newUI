'use strict';
const stages = {
  import: ['Bring in a recording.', 'Keep full sequences here. Individual note files can go straight to Loops & trim.'],
  split: ['Find the pauses between pipes.', 'Choose a source and adjust detection. Find notes proposes cuts; it does not create audio files.'],
  cuts: ['Give every pipe its own space.', 'Listen, correct boundaries and keyboard keys, and exclude unwanted sounds before creating samples.'],
  loops: ['Shape the notes. Keep the character.', 'Detect a batch or work manually. Trim the edges, refine the loop, listen, then approve.'],
  export: ['Take the prepared notes with you.', 'Export approved notes, or combine register snapshots in the mini organ player.'],
};
const splitIsVisible = () => stage === 'split' || stage === 'cuts';
let proposalId = null, stageChanging = false;

async function setStage(next) {
  if (!stages[next] || busy || splitWorking || stageChanging) return;
  stageChanging = true;
  try {
    if (splitIsVisible() && loadedSplitSourceId) await persistSplitDraft();
    stopPlayback(); stopSplitAudio();
    stage = next;
    render();
    document.querySelector(`[data-stage="${stage}"]`)?.scrollIntoView({block:'nearest', inline:'nearest'});
    if (splitIsVisible()) prepareSplitter();
    if (stage === 'export') renderExportSummary();
    scheduleProjectViewSave();
  } catch (error) { toast(error.message, true); }
  finally { stageChanging = false; }
}

function prepareSplitter() {
  const sources = recordingSamples();
  if (!sources.some(s => s.id === sourceId)) sourceId = sources[0]?.id || null;
  $('#split-source').innerHTML = sources.map(s => `<option value="${s.id}">${escapeHTML(s.name)} · ${s.duration.toFixed(1)} s</option>`).join('');
  $('#split-source').value = sourceId || '';
  $('#split-empty').hidden = !!sourceId;
  if (!sourceId) {
    loadedSplitSourceId = null; splitRegions = []; splitWarnings = [];
    $('#split-regions').replaceChildren();
    const canvas = $('#split-waveform'); canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    splitMessage('Import a full recording to begin.');
  } else if (loadedSplitSourceId !== sourceId) restoreSplitDraft(sourceId);
  else renderSplit();
  if (sourceId) splitBusy(false);
  renderFlow();
}

function renderExportSummary() {
  const list = exportCandidates();
  $('#export-count').textContent = `${list.length} approved note${list.length === 1 ? '' : 's'}`;
  $('#export-selection').textContent = selected.size ? `From ${selected.size} checked notes` : 'From the visible note list';
  if ($('#export-selection-clear')) $('#export-selection-clear').hidden = !selected.size;
  $('#export-download').disabled = busy || !list.length;
}

function renderFlow() {
  const sources = recordingSamples(), notes = noteSamples(), working = busy || splitWorking;
  const pending = targets().filter(s => !s.reviewed && !s.manual && !s.analyzed);
  const missing = targets().filter(s => s.analyzed && s.markers.loopStart == null);
  $('#detection-next').hidden = !pending.length && !missing.length;
  $('#detection-next-title').textContent = pending.length ? `${pending.length} note${pending.length === 1 ? '' : 's'} waiting for loop detection` : `${missing.length} note${missing.length === 1 ? '' : 's'} need loop review`;
  $('#detection-next-help').textContent = pending.length ? 'Splitting creates individual files. Run detection to propose attack, loop, release and tail markers, or place them manually.' : 'Detection ran, but these notes have no loop. Check short or noisy regions, adjust the minimum loop length in Detection settings, or add loop points manually.';
  $('#detect-pending').textContent = `Detect ${pending.length} unanalyzed note${pending.length === 1 ? '' : 's'}`;
  $('#detect-pending').hidden = !pending.length;
  $('#detect-pending').disabled = working;
  $('#import-stage').hidden = stage !== 'import';
  $('#split-dialog').hidden = !splitIsVisible();
  $('#loops-stage').hidden = stage !== 'loops';
  $('#export-dialog').hidden = stage !== 'export';
  $('#split-dialog').setAttribute('aria-labelledby', stage === 'cuts' ? 'step-cuts' : 'step-split');
  $$('.split-setup').forEach(node => node.hidden = stage !== 'split');
  $$('.split-review').forEach(node => node.hidden = stage !== 'cuts');
  $('#split-next').hidden = stage !== 'split';
  $('#split-heading').textContent = stage === 'cuts' ? 'Review the proposed note regions' : 'Find the notes in this recording';
  $('#stage-title').textContent = stages[stage][0];
  $('#stage-description').textContent = stages[stage][1];
  $('#stage-progress').textContent = `${sources.length} source${sources.length === 1 ? '' : 's'} · ${notes.length} notes · ${notes.filter(s => s.reviewed).length} approved`;
  $$('[data-stage]').forEach(button => {
    const current = button.dataset.stage === stage;
    button.setAttribute('aria-selected', String(current)); button.tabIndex = current ? 0 : -1;
    button.classList.toggle('current', current); button.disabled = working;
  });
  for (const button of $$('#project-send, #export-player-update')) button.disabled = working || !notes.length;
  for (const field of $$('#project-stop-name, #project-key-shift, #project-player-scope')) field.disabled = working;
  $('#source-count').textContent = `${sources.length} full recording${sources.length === 1 ? '' : 's'}`;
  $('#source-list').innerHTML = sources.map(source => {
    const children = notes.filter(n => n.source?.id === source.id).length;
    const regions = projectState?.drafts?.[source.id]?.regions?.filter(r => r.keep).length || 0;
    return `<article class="source-card"><div>${icon('wave')}<div><strong>${escapeHTML(source.name)}</strong><span>${source.duration.toFixed(1)} s · ${(source.rate / 1000).toFixed(1)} kHz · ${source.bits}-bit · ${children} note copies${regions ? ` · ${regions} draft regions` : ''}</span></div></div><div><button class="button" data-source-split="${source.id}">Split settings</button><button class="button" data-source-review="${source.id}">${regions ? 'Review splits' : 'Add cuts manually'}</button>${!children && !projectState?.drafts?.[source.id]?.regions?.length ? `<button class="text-button" data-source-note="${source.id}">Use as an individual note</button>` : ''}</div></article>`;
  }).join('') || '<p class="source-empty">No full recordings yet. Import a sequence above, or choose individual notes.</p>';
  $('#import-note-summary').textContent = `${notes.length} individual note${notes.length === 1 ? '' : 's'} available for loop detection and review.`;
  $('#import-to-loops').disabled = working || !notes.length;
  for (const node of $$('#source-list button, #import-recording, #import-notes, #bulk-approval-mode, #clear-selection, #import-kind')) node.disabled = working;
  $('#split-next').disabled = working || !splitRegions.length;
  if (!sources.length) $$('#split-dialog button, #split-dialog input, #split-dialog select').forEach(node => { if (!['split-cancel', 'split-close', 'split-save-project'].includes(node.id)) node.disabled = true; });
  $('#move-to-recordings').disabled = working || selected.size !== 1 || !targets().some(s => !s.source);
  $('#move-to-recordings').hidden = selected.size !== 1 || !targets().some(s => !s.source);
  $('#marker-at-cursor').disabled = working || !active();
  for (const node of $$('#loop-proposal input, #loop-proposal button')) node.disabled = working || !active();
  if (proposalId !== activeId) { proposalId = activeId; resetProposal(); }
  if (stage === 'export') renderExportSummary();
}

$$('[data-stage]').forEach(button => {
  button.onclick = () => setStage(button.dataset.stage);
  button.onkeydown = async event => {
    const buttons = $$('[data-stage]'), index = buttons.indexOf(button);
    const next = event.key === 'ArrowRight' ? (index + 1) % buttons.length : event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault(); await setStage(buttons[next].dataset.stage); buttons[next].focus();
  };
});
$$('[data-stage-link]').forEach(button => button.onclick = () => setStage(button.dataset.stageLink));
$('#import-top').onclick = () => setStage('import');
$('#import-top').innerHTML = icon('plus') + 'Import audio';
$('#split-open').onclick = () => setStage('split');
$('#import-to-loops').onclick = () => setStage('loops');
$('#export-side').onclick = () => setStage('export');
$('#split-next').onclick = () => setStage('cuts');
$('#import-recording').onclick = () => { $('#import-kind').value = 'recording'; $('#file-input').click(); };
$('#import-notes').onclick = () => { $('#import-kind').value = 'sample'; $('#file-input').click(); };
$('#source-list').onclick = async event => {
  const button = event.target.closest('[data-source-split], [data-source-review], [data-source-note]');
  if (!button || busy || splitWorking) return;
  if (button.dataset.sourceNote) {
    setBusy(true, 'Moving audio to individual notes…');
    try { const result = await request('/api/sample-kind', {id:button.dataset.sourceNote, kind:'sample'}); samples = result.samples; projectState = result.project; activeId = button.dataset.sourceNote; stage = 'loops'; }
    catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
    return;
  }
  sourceId = button.dataset.sourceSplit || button.dataset.sourceReview;
  await setStage(button.dataset.sourceSplit ? 'split' : 'cuts');
};
$$('[data-filter]').forEach(button => button.onclick = () => { filter = button.dataset.filter; setStage('loops'); });
$('#clear-selection').onclick = () => { selected.clear(); render(); };
$('#move-to-recordings').onclick = async () => {
  if (busy || selected.size !== 1) return;
  const id = [...selected][0]; setBusy(true, 'Moving full recording to Sources…');
  try { const result = await request('/api/sample-kind', {id, kind:'recording'}); samples = result.samples; projectState = result.project; sourceId = id; selected.clear(); stage = 'import'; }
  catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
};
$('#marker-at-cursor').onclick = () => editMarker($('#place-marker').value, position * 1000);

// Proposed points are a separate draft. Neither typing them nor searching
// changes saved markers, approval, or audio; Apply is the only mutation.
const proposal = document.createElement('details'); proposal.id = 'loop-proposal'; proposal.className = 'loop-proposal';
proposal.innerHTML = `<summary>Add or refine a loop near chosen points</summary><p>Enter approximate points or copy the cursor. Search stays near both points and balances seam quality with distance.</p>
  <label>Proposed start (ms)<span><input id="proposal-start" type="number" min="0" step="0.001" aria-label="Proposed loop start in milliseconds"><button type="button" id="proposal-start-cursor" title="Use cursor for proposed start">Cursor</button></span></label>
  <label>Proposed end (ms)<span><input id="proposal-end" type="number" min="0" step="0.001" aria-label="Proposed loop end in milliseconds"><button type="button" id="proposal-end-cursor" title="Use cursor for proposed end">Cursor</button></span></label>
  <label>Search within ± (ms)<input id="proposal-radius" type="number" value="20" min="1" max="250" step="1"></label>
  <div class="proposal-actions"><button class="button" id="proposal-reset">Use current loop / suggest range</button><button class="button" id="proposal-find">Find best nearby seam</button><button class="button" id="proposal-apply">Use these exact points</button></div>
  <small id="proposal-status" role="status">No automatic loop? Suggest a range here and apply it. Undo restores earlier markers.</small>`;
$('#loop-duration').after(proposal);

function resetProposal() {
  const s = active();
  const m = s?.markers, available = m ? m.release - m.attack : 0;
  proposal.open = !!s && m.loopStart == null;
  const start = m ? m.loopStart ?? Math.round(m.attack + available * .25) : null;
  const end = m ? m.loopEnd ?? Math.round(m.attack + available * .8) : null;
  $('#proposal-start').value = start == null ? '' : (start / s.rate * 1000).toFixed(3);
  $('#proposal-end').value = end == null ? '' : (end / s.rate * 1000).toFixed(3);
  $('#proposal-status').textContent = m?.loopStart == null ? 'No loop yet. Adjust this proposed range, then search nearby or use the exact points.' : 'Proposed points are separate from the saved loop until you apply them.';
}
$('#proposal-reset').onclick = resetProposal;
$('#proposal-start-cursor').onclick = () => { $('#proposal-start').value = (position * 1000).toFixed(3); };
$('#proposal-end-cursor').onclick = () => { $('#proposal-end').value = (position * 1000).toFixed(3); };
function proposedFrames() {
  if (!active()) return null;
  for (const id of ['proposal-start','proposal-end']) if (!$('#' + id).value || !$('#' + id).reportValidity()) return null;
  return {start:Math.round(Number($('#proposal-start').value) * active().rate / 1000), end:Math.round(Number($('#proposal-end').value) * active().rate / 1000)};
}
$('#proposal-find').onclick = async () => {
  const s = active(), points = proposedFrames();
  if (busy || !points || !$('#proposal-radius').value || !$('#proposal-radius').reportValidity()) return;
  setBusy(true, 'Comparing seams near your proposed points…');
  try {
    const result = await request('/api/loop-refine', {id:s.id, ...points, radiusMs:Number($('#proposal-radius').value), minimumLoop:settings.minimumLoop});
    loopOptions = result.candidates; loopOptionsKey = loopSearchKey(s);
    $('#loop-choice').innerHTML = loopOptions.map((c,i) => `<option value="${i}">${c.quality}% seam · start ${c.startShiftMs > 0 ? '+' : ''}${c.startShiftMs} ms · end ${c.endShiftMs > 0 ? '+' : ''}${c.endShiftMs} ms</option>`).join('') || '<option>No seam fits this range</option>';
    $('#loop-options-status').textContent = loopOptions.length ? 'Nearby suggestions found. Try & listen applies the selected pair; Undo restores the old loop.' : 'No nearby seam meets the minimum loop length. Adjust the range, search distance, or detection settings.';
    $('#proposal-status').textContent = `${loopOptions.length} nearby choices. Saved points are unchanged until Try & listen.`;
  } catch (error) { $('#proposal-status').textContent = error.message; }
  finally { setBusy(false); }
};
$('#proposal-apply').onclick = async () => {
  const s = active(), points = proposedFrames(); if (busy || !points) return;
  if (await saveEdit({...s.markers, loopStart:points.start, loopEnd:points.end}, s.note)) {
    $('#proposal-status').textContent = 'Exact points applied. Audition the loop; Undo restores earlier markers.';
    mode = 'loop'; $$('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === mode)); await play();
  }
};

$('#detect-pending').onclick = () => detect(false, true);

// app.js loads the session asynchronously. Its render calls this once ready.
renderFlow();
