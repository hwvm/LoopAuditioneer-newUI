'use strict';
const instrumentChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('loop-instrument') : null;

async function sendProjectToPlayer(identity = null) {
  let completed = false;
  if (busy || splitWorking || projectOperating) return;
  if ((!identity || identity === projectState.id) && $('#project-player-scope').value === 'selected') {
    if (!selected.size) { toast('Check one take per key in Loops & trim, then update the player.', true); return; }
    projectState.player = {...projectState.player, sampleIds:[...selected]};
  }
  await projectOperation(async () => {
    const current = !identity || identity === projectState.id;
    const body = current
      ? {current:true, name:$('#project-stop-name').value.trim() || projectState.name, keyShift:Number($('#project-key-shift').value)}
      : {projectId:identity};
    const result = await request('/api/player/sync', body);
    projectState = result.project; workspaceToken = result.token;
    const stop = result.stop;
    const message = `${stop.name}: ${stop.samples.length} pipes sent to the player${stop.skipped.length ? `; ${stop.skipped.length} notes skipped (missing key or valid loop)` : ''}. Open Play to listen.`;
    $('#project-player-status').textContent = message;
    $('#project-library-status').textContent = message;
    instrumentChannel?.postMessage({type:'updated', stopId:stop.id});
    toast(message);
    completed = true;
  }, 'Saving the project and updating its player stop…');
  return completed;
}
$('#project-send').onclick = () => sendProjectToPlayer();
$('#export-player-update').onclick = () => sendProjectToPlayer();
$('#project-stop-name').onchange = scheduleProjectViewSave;
$('#project-key-shift').onchange = scheduleProjectViewSave;
$('#project-player-scope').onchange = scheduleProjectViewSave;
const resetExportSelection = document.createElement('button');
resetExportSelection.id = 'export-selection-clear'; resetExportSelection.hidden = !selected.size;
resetExportSelection.className = 'text-button'; resetExportSelection.textContent = 'Clear note selection';
resetExportSelection.onclick = () => { selected.clear(); render(); renderExportSummary(); };
$('#export-selection').after(resetExportSelection);

async function openRequestedProject() {
  const identity = new URLSearchParams(location.search).get('project');
  if (!identity) return;
  if (!/^[a-f0-9]{24}$/.test(identity)) throw new Error('Invalid project link. Choose a project from Projects.');
  if (projectState.id !== identity) {
    await projectOperation(async () => acceptProject(await request('/api/project-open', {id:identity})), 'Opening the stop’s register project…');
  }
  history.replaceState(null, '', '/');
}
