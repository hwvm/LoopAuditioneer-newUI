'use strict';
let projectQueue = Promise.resolve(), projectViewTimer, lastProjectView = '', restoringProject = false;
let projectWriteError = null, projectNameAction = 'new';
let projectOperating = false, pendingProjectWrites = 0;

function enqueueProjectWrite(action) {
  pendingProjectWrites++;
  const task = projectQueue.then(action).finally(() => { pendingProjectWrites--; });
  projectQueue = task.catch((error) => { projectWriteError = error; $('#save-state').textContent = 'Save failed'; });
  return task;
}

function projectDetails() {
  return {name: $('#project-name').value.trim(), notes: $('#project-notes').value, settings: {...settings},
    ui: {activeId, stage, sourceId, selected:[...selected], filter, search:$('#search').value, mode, volume:Number($('#volume').value)},
    export: {mode:$('input[name="export-mode"]:checked').value, trim:$('#export-trim').checked},
    player: {name:$('#project-stop-name').value.trim(), keyShift:Number($('#project-key-shift').value),
      scope:$('#project-player-scope').value, sampleIds:projectState?.player?.sampleIds || []}};
}

function restoreProjectUI() {
  if (!projectState) return;
  restoringProject = true;
  $('#project-name').value = projectState.name;
  $('#project-notes').value = projectState.notes || '';
  $('#project-player-status').textContent = 'Save your progress and send playable notes to the instrument. Drafts can be auditioned.';
  settings = {...projectState.settings};
  const ui = projectState.ui || {};
  activeId = noteSamples().some(s => s.id === ui.activeId) ? ui.activeId : noteSamples()[0]?.id || null;
  stage = ui.stage || (noteSamples().length ? 'loops' : 'import');
  sourceId = recordingSamples().some(s => s.id === ui.sourceId) ? ui.sourceId : recordingSamples()[0]?.id || null;
  selected = new Set((ui.selected || []).filter((id) => samples.some((s) => s.id === id)));
  filter = ui.filter || 'all'; $('#search').value = ui.search || ''; mode = ui.mode || 'full';
  $('#volume').value = ui.volume ?? 65;
  $$('[data-mode]').forEach((button) => button.classList.toggle('active', button.dataset.mode === mode));
  const format = projectState.export || {mode:'wav', trim:false};
  $(`input[name="export-mode"][value="${format.mode}"]`).checked = true;
  $('#export-trim').checked = format.trim;
  $('#project-stop-name').value = projectState.player?.name || '';
  $('#project-stop-name').placeholder = projectState.name;
  $('#project-key-shift').value = projectState.player?.keyShift || 0;
  $('#project-player-scope').value = projectState.player?.scope || 'all';
  lastProjectView = JSON.stringify(projectDetails());
  restoringProject = false;
}

async function persistProjectDetails() {
  if (!projectState) throw new Error('Restart the server and refresh the page to enable projects.');
  const details = projectDetails(), serialized = JSON.stringify(details);
  if (!details.name) throw new Error('Give your register a project name.');
  const result = await enqueueProjectWrite(() => request('/api/project-details', details));
  // Keep locally pending drafts until their queued writes complete.
  projectState = {...result.project, drafts: {...result.project.drafts, ...projectState.drafts}};
  lastProjectView = serialized;
}

function scheduleProjectViewSave() {
  if (!projectState || restoringProject || busy) return;
  clearTimeout(projectViewTimer); projectViewTimer = null;
  if (JSON.stringify(projectDetails()) === lastProjectView) return;
  projectViewTimer = setTimeout(() => { projectViewTimer = null; persistProjectDetails().catch((error) => toast(error.message, true)); }, 350);
}

async function flushProjectEdits() {
  clearTimeout(projectViewTimer); projectViewTimer = null;
  const sample = active();
  if (sample) {
    const markers = {};
    for (const key of markerKeys) {
      const value = $('#marker-' + key).value;
      markers[key] = value === '' ? null : Math.round(Number(value) / 1000 * sample.rate);
    }
    const note = $('#root-note').value === '' ? null : Number($('#root-note').value);
    if (markerKeys.some((key) => markers[key] !== sample.markers[key]) || note !== sample.note) {
      if (!await saveEdit(markers, note)) throw new Error('Correct the sample marker fields before saving this project.');
    }
  }
  if (splitIsVisible() && typeof persistSplitDraft === 'function') await persistSplitDraft();
  await projectQueue;
  if (projectWriteError) {
    const error = projectWriteError; projectWriteError = null;
    throw new Error('A recent edit could not be saved. Retry Save project before switching registers. ' + error.message);
  }
  await persistProjectDetails();
}

async function projectOperation(action, message) {
  if (busy || splitWorking || projectOperating) return;
  projectOperating = true;
  try {
    await flushProjectEdits();
    setBusy(true, message);
    $$('#projects-dialog button, #project-name-dialog button, #project-name-dialog input, #projects-dialog textarea').forEach((b) => b.disabled = true);
    if (splitIsVisible()) splitBusy(true);
    await action();
  } catch (error) { toast(error.message, true); $('#project-library-status').textContent = error.message; }
  finally {
    $$('#projects-dialog button, #project-name-dialog button, #project-name-dialog input, #projects-dialog textarea').forEach((b) => b.disabled = false);
    if (splitIsVisible()) splitBusy(false);
    projectOperating = false; setBusy(false);
  }
}

function acceptProject(data) {
  stopPlayback(); stopSplitAudio();
  samples = data.samples; projectState = data.project; workspaceToken = data.token;
  undoHistory.clear(); bufferId = null; audioBuffer = null; position = 0; zoom = 1; viewStart = 0;
  splitRegions = []; splitWarnings = []; splitCursor = null; splitIndex = 0; loadedSplitSourceId = null;
  clearTimeout(splitDraftTimer);
  $('#zoom-reset').textContent = '1×';
  restoreProjectUI(); render();
  if (splitIsVisible() && typeof prepareSplitter === 'function') prepareSplitter();
}

async function saveCurrentProject() {
  await projectOperation(async () => {
    const result = await request('/api/project-save', {}); projectState = result.project; workspaceToken = result.token;
    $('#project-name').value = projectState.name;
    toast(`Project “${projectState.name}” saved with audio and edits.`);
  }, 'Saving register project…');
}

async function refreshProjectLibrary() {
  const [result, instrument] = await Promise.all([request('/api/projects'), request('/api/player')]);
  $('#project-list').innerHTML = result.projects.map((project) => {
    const stop = instrument.stops.find(s => s.projectId === project.id);
    const counts = project.notes === undefined ? `${project.samples} files` : `${project.sources} sources · ${project.notes} notes`;
    return `<div role="listitem" class="project-card"><div><strong>${escapeHTML(project.name)}</strong><span>${counts} · ${project.approved} approved</span><span>${stop ? (stop.syncState === 'current' ? 'In player · up to date' : 'In player · update available') : 'Not linked to player'} · Saved ${escapeHTML(new Date(project.savedAt).toLocaleString())}</span></div><div class="project-card-actions"><button class="button" data-open-project="${escapeHTML(project.id)}">${project.id === projectState?.id ? 'Continue editing' : 'Edit project'}</button><button class="button" data-audition-project="${escapeHTML(project.id)}">${stop ? 'Update player' : 'Add to player'}</button></div></div>`;
  }).join('') || '<p class="project-library-hint">No saved projects yet. Save this register to keep it in your library.</p>';
  $('#project-library-status').textContent = projectState?.savedAt ? `Last project save: ${new Date(projectState.savedAt).toLocaleString()}. New edits also autosave in the current session.` : 'The current session autosaves locally. Save project to add it to this library.';
}

$('#project-save').onclick = saveCurrentProject;
$('#split-save-project').onclick = saveCurrentProject;
$('#project-name').addEventListener('change', scheduleProjectViewSave);
$('#project-notes').addEventListener('change', scheduleProjectViewSave);
$('#volume').addEventListener('change', scheduleProjectViewSave);
$$('input[name="export-mode"], #export-trim').forEach((input) => input.addEventListener('change', scheduleProjectViewSave));
$('#settings-save').addEventListener('click', scheduleProjectViewSave);
$('#project-open').onclick = async () => {
  if (busy) return;
  $('#projects-dialog').showModal();
  try { await refreshProjectLibrary(); } catch (error) { $('#project-library-status').textContent = error.message; }
};

function askProjectName(action) {
  if (busy) return;
  projectNameAction = action;
  $('#project-name-title').textContent = action === 'copy' ? 'Save a project copy' : 'New register';
  $('#project-name-description').textContent = action === 'copy' ? 'Keep this register and its progress, then continue in an independent copy.' : 'Your current register will be saved before starting a new one.';
  $('#project-name-entry').value = action === 'copy' ? ($('#project-name').value + ' — copy').slice(0,120) : '';
  $('#project-name-confirm').textContent = action === 'copy' ? 'Save a copy' : 'Create project';
  $('#project-name-dialog').showModal(); $('#project-name-entry').focus();
}
$('#project-new').onclick = () => askProjectName('new');
$('#project-copy').onclick = () => askProjectName('copy');
$('#project-name-confirm').onclick = async () => {
  if (!$('#project-name-entry').reportValidity() || !$('#project-name-entry').value.trim()) return;
  const name = $('#project-name-entry').value.trim();
  await projectOperation(async () => {
    if (projectNameAction === 'new') acceptProject(await request('/api/project-new', {name}));
    else {
      // Save the original before branching, including edits since the last save.
      await request('/api/project-save', {});
      const result = await request('/api/project-save', {name, copy:true});
      projectState = result.project; workspaceToken = result.token; $('#project-name').value = projectState.name;
      lastProjectView = JSON.stringify(projectDetails());
    }
    $('#project-name-dialog').close(); $('#projects-dialog').close();
    toast(projectNameAction === 'new' ? `“${name}” is ready. Import the register recording.` : `Now working in “${name}”. The original project is saved separately.`);
  }, 'Saving project and switching registers…');
};
$('#project-list').addEventListener('click', async (event) => {
  const audition = event.target.closest('[data-audition-project]');
  if (audition) { if (await sendProjectToPlayer(audition.dataset.auditionProject)) await refreshProjectLibrary(); return; }
  const button = event.target.closest('[data-open-project]'); if (!button) return;
  await projectOperation(async () => {
    acceptProject(await request('/api/project-open', {id:button.dataset.openProject}));
    $('#projects-dialog').close(); toast(`Opened “${projectState.name}”. Continue where you left off.`);
  }, 'Opening register project…');
});
$('#project-download').onclick = async () => {
  await projectOperation(async () => {
    const bytes = await request('/api/project-download', {}, true);
    const url = URL.createObjectURL(new Blob([bytes], {type:'application/octet-stream'}));
    const link = document.createElement('a'); link.href = url;
    link.download = ($('#project-name').value.replace(/[^\p{L}\p{N} ._-]/gu, '_') || 'register') + '.loopproject';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast('Portable project downloaded, including source recordings and edits.');
    await refreshProjectLibrary();
  }, 'Packaging audio and project edits…');
};
$('#project-import').onclick = () => $('#project-file-input').click();
$('#project-file-input').onchange = async (event) => {
  const file = event.target.files[0]; if (!file) return;
  if (!file.name.toLowerCase().endsWith('.loopproject')) return toast('Choose a .loopproject file.', true);
  if (file.size > 1040 * 1024 * 1024) return toast('Project file exceeds the supported size.', true);
  await projectOperation(async () => {
    const response = await fetch('/api/project-import', {method:'POST', headers:{'X-Loop-Workspace':'1', ...(workspaceToken ? {'X-Loop-Project':workspaceToken} : {})}, body:file});
    const data = await response.json(); if (!response.ok) throw new Error(data.error);
    acceptProject(data); $('#projects-dialog').close();
    toast('Project imported as a separate working copy. Save project to add it to your library.');
  }, 'Checking and opening portable project…');
  $('#project-file-input').value = '';
};
document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {event.preventDefault(); saveCurrentProject();}
});
if (projectState) restoreProjectUI();
window.addEventListener('beforeunload', (event) => {
  if (projectOperating || pendingProjectWrites || projectViewTimer || splitDraftTimer) { event.preventDefault(); event.returnValue = ''; }
});
