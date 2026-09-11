'use strict';
// Reviewable split drafts are autosaved into the current register project.
let splitRegions = [], splitWarnings = [], splitIndex = 0, splitCursor = null, splitWorking = false;
let splitDraftTimer = null, loadedSplitSourceId = null;
let splitViewStart = 0, splitViewEnd = null;
const splitSample = () => samples.find((s) => s.id === $('#split-source').value);
const keptRegions = () => splitRegions.filter((r) => r.keep);

function splitMessage(message, error = false) {
  $('#split-summary').textContent = message;
  $('#split-summary').classList.toggle('split-error', error);
}

function stopSplitAudio() {
  $('#split-audio').pause();
  $('#split-audio').removeAttribute('src');
  $('#split-audio').load();
  $('#split-playing').textContent = 'Select Listen to preview one cut, including its tail.';
}

function splitBusy(value) {
  splitWorking = value;
  $$('#split-dialog button, #split-dialog input, #split-dialog select').forEach((control) => control.disabled = value);
  $('#split-threshold').disabled = value || $('#split-auto').checked;
  if (value) stopSplitAudio();
  renderSplit();
  if (typeof renderFlow === 'function') renderFlow();
}

function splitValidation() {
  const sample = splitSample(), regions = keptRegions();
  if (!regions.length) return 'Keep at least one region to create note samples.';
  let previous = 0;
  for (const [index, r] of regions.entries()) {
    if (!Number.isInteger(r.start) || !Number.isInteger(r.end) || r.start < 0 || r.end > sample.frames || r.end - r.start < 16) return `Region ${index + 1}: start and end must enclose at least 16 frames inside the recording.`;
    if (r.start < previous) return `Region ${index + 1} overlaps the previous kept region. Adjust or merge it.`;
    if (r.note !== null && (!Number.isInteger(r.note) || r.note < 0 || r.note > 127)) return `Region ${index + 1}: use a MIDI note from 0 to 127, or leave it blank.`;
    previous = r.end;
  }
  return '';
}

function renderSplit() {
  const sample = splitSample();
  if (!sample) return;
  splitIndex = Math.max(0, Math.min(splitRegions.length - 1, splitIndex));
  $('#split-regions').innerHTML = splitRegions.map((r, index) => `<tr data-region="${index}" class="${index === splitIndex ? 'region-active' : ''}"><td><input type="checkbox" data-region-keep="${index}" aria-label="Keep region ${index + 1}" ${r.keep ? 'checked' : ''} ${splitWorking ? 'disabled' : ''}></td><td><button class="region-select" data-region-select="${index}" ${splitWorking ? 'disabled' : ''}>${String(index + 1).padStart(2,'0')} <span>${((r.end - r.start) / sample.rate).toFixed(2)} s</span></button></td><td><input type="number" min="0" step="0.001" data-region-field="start" data-region-index="${index}" aria-label="Region ${index + 1} start in milliseconds" value="${Number.isFinite(r.start) ? (r.start / sample.rate * 1000).toFixed(3) : ''}" ${splitWorking ? 'disabled' : ''}></td><td><input type="number" min="0" step="0.001" data-region-field="end" data-region-index="${index}" aria-label="Region ${index + 1} end in milliseconds" value="${Number.isFinite(r.end) ? (r.end / sample.rate * 1000).toFixed(3) : ''}" ${splitWorking ? 'disabled' : ''}></td><td><div class="region-note"><input type="number" min="0" max="127" step="1" data-region-field="note" data-region-index="${index}" aria-label="Region ${index + 1} MIDI note" value="${r.note ?? ''}" placeholder="—" ${splitWorking ? 'disabled' : ''}><span>${r.note == null ? 'Unassigned' : noteName(r.note)}<small>${r.note == null ? 'needs a key' : r.confidence != null ? 'suggested' : 'assigned'}</small></span></div></td><td><button class="icon-button" data-region-listen="${index}" aria-label="Listen to region ${index + 1}" ${splitWorking ? 'disabled' : ''}>${icon('play')}</button></td></tr>`).join('') || '<tr><td colspan="6" class="split-no-regions">Find notes, or add a region and divide it manually.</td></tr>';
  const count = keptRegions().length;
  const copies = samples.filter(s => s.source?.id === sample.id).length;
  $('#split-create').textContent = `${copies ? 'Create new copies' : 'Create note samples'} (${count})`;
  $('#split-create').disabled = splitWorking || !!splitValidation();
  $('#split-divide').disabled = splitWorking || !splitRegions.length;
  $('#split-merge').disabled = splitWorking || splitIndex >= splitRegions.length - 1;
  $('#split-assign').disabled = splitWorking || !count;
  $('#split-cursor').textContent = splitCursor == null ? 'Click the waveform to place a cursor' : `Cursor: ${(splitCursor / sample.rate).toFixed(3)} s`;
  $('#split-warnings').innerHTML = splitWarnings.map((warning) => `<p>${escapeHTML(warning)}</p>`).join('');
  drawSplitWave();
}

function noteSplitEdited() {
  stopSplitAudio();
  const error = splitValidation();
  splitMessage(error || `${keptRegions().length} regions kept · draft autosaves; sample creation is separate`, !!error);
  renderSplit();
  scheduleSplitDraftSave();
}

async function findSplitRegions() {
  if (splitWorking || !splitSample()) return;
  for (const input of $$('.split-settings input')) if (!input.reportValidity()) return;
  splitBusy(true);
  splitMessage('Finding quiet gaps and suggesting root notes…');
  try {
    const result = await request('/api/split-preview', {id: splitSample().id, autoThreshold:$('#split-auto').checked, threshold:Number($('#split-threshold').value), gapMs:Number($('#split-gap').value), minimumMs:Number($('#split-minimum').value), tailMs:Number($('#split-tail').value)});
    splitRegions = result.regions.map((r) => ({...r, keep:true})); splitWarnings = result.warnings; splitIndex = 0; splitCursor = null;
    splitMessage(`${splitRegions.length} note region${splitRegions.length === 1 ? '' : 's'} found · listen and check the cuts`);
  } catch (error) { splitMessage(error.message === 'Unknown operation.' ? 'Restart server.py to load note splitting, then try Find notes again.' : error.message, true); }
  finally { splitBusy(false); scheduleSplitDraftSave(); }
}

function openSplitter() { return setStage('split'); }
async function closeSplitter() { return setStage(stage === 'cuts' ? 'split' : 'import'); }

function captureSplitDraft() {
  return {regions: splitRegions.map((r) => ({...r})), warnings:[...splitWarnings], index:splitIndex, cursor:splitCursor,
    settings:{autoThreshold:$('#split-auto').checked, threshold:Number($('#split-threshold').value), gapMs:Number($('#split-gap').value), minimumMs:Number($('#split-minimum').value), tailMs:Number($('#split-tail').value), first:Number($('#split-first').value), step:Number($('#split-step').value)},
    analyzeAfter:$('#split-analyze').checked};
}

function scheduleSplitDraftSave() {
  clearTimeout(splitDraftTimer);
  if (!loadedSplitSourceId || !projectState || splitWorking) return;
  projectState.drafts[loadedSplitSourceId] = captureSplitDraft();
  splitDraftTimer = setTimeout(() => { splitDraftTimer = null; persistSplitDraft().catch((error) => splitMessage('Draft save failed: ' + error.message, true)); }, 300);
}

async function persistSplitDraft() {
  clearTimeout(splitDraftTimer); splitDraftTimer = null;
  if (!loadedSplitSourceId || !projectState) return;
  const id = loadedSplitSourceId, draft = captureSplitDraft();
  projectState.drafts[id] = draft;
  await enqueueProjectWrite(() => request('/api/project-draft', {id, draft}));
}

function restoreSplitDraft(id) {
  loadedSplitSourceId = id;
  splitViewStart = 0; splitViewEnd = null;
  const draft = projectState?.drafts?.[id];
  if (draft) {
    splitRegions = draft.regions.map((r) => ({...r})); splitWarnings = [...draft.warnings]; splitIndex = draft.index; splitCursor = draft.cursor;
    const ids = {threshold:'threshold', gapMs:'gap', minimumMs:'minimum', tailMs:'tail', first:'first', step:'step'};
    for (const [key, input] of Object.entries(ids)) $('#split-' + input).value = draft.settings[key];
    $('#split-auto').checked = draft.settings.autoThreshold ?? true;
    $('#split-threshold').disabled = $('#split-auto').checked;
    $('#split-analyze').checked = draft.analyzeAfter;
    splitMessage('Saved split draft restored. Continue editing, or Find notes to replace these suggestions.'); renderSplit();
  } else {
    splitRegions = []; splitWarnings = []; splitIndex = 0; splitCursor = null; $('#split-analyze').checked = false; renderSplit(); splitMessage('Choose settings and Find notes, or use Review splits to add regions manually.');
  }
}

function drawSplitWave() {
  const canvas = $('#split-waveform'), sample = splitSample();
  if (!sample || !splitIsVisible()) return;
  const rect = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
  if (!rect.width || !rect.height) return;
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext('2d'); ctx.scale(ratio,ratio);
  const width = rect.width, height = rect.height, pad = 12, plotWidth = width - pad * 2;
  const viewEnd = splitViewEnd ?? sample.frames, span = viewEnd - splitViewStart;
  const px = (frame) => pad + (frame - splitViewStart) / span * plotWidth;
  for (const [index, r] of splitRegions.entries()) {
    if (!Number.isFinite(r.start) || !Number.isFinite(r.end)) continue;
    ctx.fillStyle = r.keep ? (index === splitIndex ? '#a5cd7044' : '#789b5122') : '#78625122';
    ctx.fillRect(px(r.start),20,px(r.end)-px(r.start),height-43);
    ctx.strokeStyle = index === splitIndex ? '#cee89f' : '#738c56';
    ctx.strokeRect(px(r.start),20,px(r.end)-px(r.start),height-43);
    if (px(r.end)-px(r.start)>16) { ctx.fillStyle='#c8ddb0'; ctx.font='9px Segoe UI'; ctx.fillText(String(index+1),px(r.start)+4,14); }
  }
  sample.peaks.forEach((channel, c) => {
    const channelHeight = (height - 55) / sample.channels, middle = 28 + channelHeight * (c+.5);
    ctx.strokeStyle = '#9eae8b'; ctx.lineWidth=.7;
    for (let x=0;x<plotWidth;x++) {
      const a=Math.max(0,Math.floor((splitViewStart+x/plotWidth*span)/sample.frames*channel.length)),b=Math.min(channel.length,Math.max(a+1,Math.ceil((splitViewStart+(x+1)/plotWidth*span)/sample.frames*channel.length)));
      let low=0,high=0;
      for(let i=a;i<b;i++){low=Math.min(low,channel[i][0]);high=Math.max(high,channel[i][1]);}
      ctx.beginPath();ctx.moveTo(pad+x,middle-high*channelHeight*.8);ctx.lineTo(pad+x,middle-low*channelHeight*.8);ctx.stroke();
    }
  });
  ctx.font='9px Segoe UI';ctx.fillStyle='#95a782';
  for(let i=0;i<=6;i++){ctx.textAlign=i===6?'right':i===0?'left':'center';ctx.fillText(((splitViewStart+span*i/6)/sample.rate).toFixed(2)+'s',pad+plotWidth*i/6,height-6);}
  if(splitCursor!=null){ctx.strokeStyle='#f4efcf';ctx.beginPath();ctx.moveTo(px(splitCursor),20);ctx.lineTo(px(splitCursor),height-23);ctx.stroke();}
}

$('#split-open').onclick = openSplitter;
$('#split-close').onclick = $('#split-cancel').onclick = closeSplitter;
$('#split-dialog').addEventListener('cancel',(event)=>{event.preventDefault(); closeSplitter();});
$('#split-find').onclick = findSplitRegions;
$('#split-source').onchange = async () => { const next = $('#split-source').value; try { await persistSplitDraft(); stopSplitAudio(); sourceId = next; restoreSplitDraft(next); scheduleProjectViewSave(); } catch (error) { $('#split-source').value = loadedSplitSourceId; splitMessage(error.message, true); } };
$$('.split-settings input').forEach((input)=>input.onchange=()=>{splitMessage('Settings changed. Find notes again to replace the preview with new suggestions.');scheduleSplitDraftSave();});
$('#split-auto').onchange = () => {
  $('#split-threshold').disabled = $('#split-auto').checked;
  splitMessage('Threshold mode changed. Find notes again to update the suggestions.');
  scheduleSplitDraftSave();
};
$$('#split-first, #split-step, #split-analyze').forEach((input) => input.addEventListener('change', scheduleSplitDraftSave));
$('#split-regions').addEventListener('change',(event)=>{
  if(splitWorking) return;
  const input=event.target;
  if(input.dataset.regionKeep!==undefined) splitRegions[Number(input.dataset.regionKeep)].keep=input.checked;
  if(input.dataset.regionField){
    const r=splitRegions[Number(input.dataset.regionIndex)], key=input.dataset.regionField;
    r[key]=input.value===''?null:(key==='note'?Number(input.value):Math.round(Number(input.value)/1000*splitSample().rate));
    r.confidence=null;
  }
  noteSplitEdited();
});
$('#split-regions').addEventListener('input', (event) => {
  if (splitWorking || !event.target.dataset.regionField) return;
  const input = event.target, r = splitRegions[Number(input.dataset.regionIndex)], key = input.dataset.regionField;
  r[key] = input.value === '' ? null : key === 'note' ? Number(input.value) : Math.round(Number(input.value) / 1000 * splitSample().rate);
  r.confidence = null;
  // Keep focus while typing; update the draft without replacing its input elements.
  const error = splitValidation();
  $('#split-create').disabled = !!error;
  splitMessage(error || 'Timing and pitch edits are autosaving to this project.', !!error);
  drawSplitWave(); scheduleSplitDraftSave();
});
$('#split-regions').addEventListener('click',(event)=>{
  if(splitWorking)return;
  const select=event.target.closest('[data-region-select]'), listen=event.target.closest('[data-region-listen]');
  if(select){splitIndex=Number(select.dataset.regionSelect);splitCursor=Math.round((splitRegions[splitIndex].start+splitRegions[splitIndex].end)/2);renderSplit();scheduleSplitDraftSave();}
  if(listen){
    splitIndex=Number(listen.dataset.regionListen);const r=splitRegions[splitIndex],s=splitSample();
    if(!Number.isInteger(r.start)||!Number.isInteger(r.end)||r.start<0||r.end>s.frames||r.end<=r.start)return splitMessage('Fix this region’s start and end before listening.',true);
    stopSplitAudio();
    $('#split-audio').src=`/api/audio/${s.id}?start=${r.start}&end=${r.end}`;
    $('#split-audio').volume=Number($('#volume').value)/100;
    $('#split-audio').play().catch(()=>splitMessage('Preview could not play. Check the local server and region boundaries.',true));
    $('#split-playing').textContent=`Region ${splitIndex+1} · ${noteName(r.note)} · ${((r.end-r.start)/s.rate).toFixed(2)} s`;
    renderSplit();
  }
});
$('#split-assign').onclick=()=>{
  if(!$('#split-first').reportValidity()||!$('#split-step').reportValidity())return;
  const first=Number($('#split-first').value),step=Number($('#split-step').value),regions=keptRegions();
  if(regions.some((_,i)=>first+i*step<0||first+i*step>127))return splitMessage('That sequence goes outside MIDI notes 0–127. Change the first note or step.',true);
  regions.forEach((r,i)=>{r.note=first+i*step;r.confidence=null;});noteSplitEdited();
};
$('#split-add').onclick=()=>{
  const s=splitSample();if(!s)return;let start=0,end=s.frames;
  for(const r of splitRegions){if(r.start-start>=16){end=r.start;break;}start=Math.max(start,r.end);}
  if(end-start<16)return splitMessage('No unused space remains. Divide an existing region instead.',true);
  splitRegions.push({start,end,note:null,confidence:null,keep:true});splitRegions.sort((a,b)=>a.start-b.start);splitIndex=splitRegions.findIndex((r)=>r.start===start);splitCursor=Math.round((start+end)/2);noteSplitEdited();
};
$('#split-divide').onclick=()=>{
  const r=splitRegions[splitIndex];if(!r)return;
  const cut=splitCursor??Math.round((r.start+r.end)/2);
  if(cut-r.start<16||r.end-cut<16)return splitMessage('Place the cursor inside the selected region, at least 16 frames from either end.',true);
  splitRegions.splice(splitIndex,1,{...r,end:cut,note:null,confidence:null},{...r,start:cut,note:null,confidence:null});noteSplitEdited();
};
$('#split-merge').onclick=()=>{
  const a=splitRegions[splitIndex],b=splitRegions[splitIndex+1];if(!a||!b)return;
  splitRegions.splice(splitIndex,2,{start:a.start,end:Math.max(a.end,b.end),note:null,confidence:null,keep:a.keep||b.keep});splitCursor=null;noteSplitEdited();
};
$('#split-waveform').addEventListener('pointerdown',(event)=>{
  if(splitWorking)return;
  const s=splitSample(),rect=event.currentTarget.getBoundingClientRect();if(!s)return;
  splitCursor=Math.max(0,Math.min(s.frames,Math.round(splitViewStart+(event.clientX-rect.left-12)/(rect.width-24)*((splitViewEnd??s.frames)-splitViewStart))));
  const index=splitRegions.findIndex((r)=>r.start<=splitCursor&&r.end>splitCursor);if(index>=0)splitIndex=index;renderSplit();scheduleSplitDraftSave();
});
new ResizeObserver(drawSplitWave).observe($('#split-waveform'));
$('#split-create').onclick=async()=>{
  const error=splitValidation();if(splitWorking||busy||error)return splitMessage(error||'Wait for the current operation.',true);
  const identity=splitSample().id,regions=keptRegions().map(({start,end,note})=>({start,end,note})), analyzeAfter=$('#split-analyze').checked;
  splitBusy(true);setBusy(true,'Creating individual note samples…');splitMessage('Creating local copies. The source recording stays intact…');
  let created=false;
  try{
    await persistSplitDraft();
    const result=await request('/api/split-create',{id:identity,regions});samples=result.samples;selected=new Set(result.created);activeId=result.created[0];filter='all';$('#search').value='';bufferId=null;audioBuffer=null;position=0;zoom=1;viewStart=0;$('#zoom-reset').textContent='1×';created=true;
    stage = 'loops';stopSplitAudio();toast(`${result.created.length} note samples created. ${analyzeAfter ? 'Finding their loops next...' : 'Next: use Detect loops in Loops & trim, or add markers manually.'}`);
  }catch(error){splitMessage(error.message,true);}
  finally{splitBusy(false);setBusy(false);}
  if(created&&analyzeAfter)await detect();
};

$('#split-zoom-note').onclick = () => {
  const s = splitSample(), r = splitRegions[splitIndex]; if (!s || !r || !Number.isFinite(r.start) || !Number.isFinite(r.end) || r.end <= r.start) return;
  const margin = Math.max(s.rate * .1, (r.end - r.start) * .15);
  splitViewStart = Math.max(0, r.start - margin); splitViewEnd = Math.min(s.frames, r.end + margin); drawSplitWave();
};
$('#split-zoom-all').onclick = () => { splitViewStart = 0; splitViewEnd = null; drawSplitWave(); };
