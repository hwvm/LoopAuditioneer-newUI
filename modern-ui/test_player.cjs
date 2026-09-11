const test = require('node:test');
const assert = require('node:assert/strict');
const {OrganPlayer, smoothLoop} = require('./static/player-engine.js');
class Param {
  constructor() { this.value = 0; this.events = []; }
  setValueAtTime(v, t) { this.value = v; this.events.push([v,t]); }
  linearRampToValueAtTime(v,t) { this.events.push([v,t]); }
  cancelAndHoldAtTime() {}
}
class Node {
  constructor() { for (const p of ['gain','playbackRate','threshold','knee','ratio','attack','release']) this[p] = new Param(); }
  connect(target) { return target; }
  disconnect() { this.disconnected = true; }
  start() { this.started = true; }
  stop(time) { this.stopTime = time; }
}
function instrument() {
  const ctx = {currentTime: 1, destination: new Node(), createGain: () => new Node(), createDynamicsCompressor: () => new Node(), createBufferSource: () => new Node()};
  const player = new OrganPlayer(ctx);
  for (const id of ['16','8','4']) {
    player.addStop({id}, [{key: 60, loopStart:.2, loopEnd:1, gain:.5, buffer:{}}]);
    player.enable(id, true);
  }
  return player;
}
test('replacing an instrument disposes voices and disconnects every audio route', () => {
  const p = instrument(); p.noteOn(60); p.midi([0xb0,64,127]);
  const voices = [...p.voices]; p.destroy();
  assert.equal(p.voices.size, 0); assert.equal(p.held.size, 0); assert.equal(p.pedals.size, 0);
  assert.ok(voices.every(v => v.source.disconnected && v.gain.disconnected));
  assert.ok([...p.stops.values()].every(s => s.gain.disconnected));
  assert.ok(p.master.disconnected && p.compressor.disconnected);
});
test('three stops layer and velocity-zero releases only the owning device/channel', () => {
  const p = instrument();
  p.midi([0x90,60,100], 'one'); p.midi([0x91,60,100], 'two');
  assert.equal(p.voices.size, 6);
  p.midi([0x90,60,0], 'one');
  assert.equal([...p.voices].filter(v => !v.ending).length, 3);
  assert.equal(p.held.size, 1);
  p.midi([0x81,60,100], 'two'); assert.equal(p.held.size, 0);
});
test('sustain holds released notes and lifting pedal leaves physically held notes', () => {
  const p = instrument();
  p.midi([0xb0,64,127]); p.midi([0x90,60,100]); p.midi([0x80,60,0]);
  assert.equal([...p.voices].filter(v => !v.ending).length, 3);
  p.midi([0xb0,64,0]); assert.equal(p.held.size,0);
  p.midi([0x90,60,100]); p.midi([0xb0,64,127]); p.midi([0xb0,64,0]);
  assert.equal(p.held.size,1);
});
test('stop changes while a chord is held add and fade the correct voices', () => {
  const p = instrument(); p.noteOn(60); p.enable('4',false);
  assert.equal([...p.voices].filter(v => !v.ending).length,2);
  p.enable('4',true); assert.equal([...p.voices].filter(v => !v.ending).length,3);
  p.allOff(); assert.equal(p.held.size,0); assert.equal(p.pedals.size,0);
  assert.ok([...p.voices].every(v => v.ending));
});
test('extension is explicit and uses key distance, without double-transposing footage', () => {
  const p = instrument(); p.noteOn(61); assert.equal(p.voices.size,0);
  p.extension = 2; p.noteOn(62);
  assert.equal(p.voices.size,3);
  assert.equal([...p.voices][0].source.playbackRate.value, 2 ** (2/12));
  p.noteOn(60); assert.equal([...p.voices].at(-1).source.playbackRate.value,1);
});
test('repeated notes, channel all-off, disconnect panic and voice stealing are bounded', () => {
  const p = instrument(); p.maxVoices = 6;
  for (let i=0;i<20;i++) p.midi([0x90,60,100]);
  assert.equal(p.voices.size,6); assert.equal(p.held.size,1);
  p.midi([0xb0,123,0]); assert.equal(p.held.size,0);
  for (const voice of [...p.voices]) voice.source.onended();
  assert.equal(p.voices.size,0);
  p.midi([0x90,60,100]); p.midi([0xb0,64,127]); p.allOff();
  assert.equal(p.pedals.size,0); assert.equal(p.held.size,0);
});
test('preview loop blend joins adjacent samples and preserves the rest of stereo buffer', () => {
  const data = [Float32Array.from({length:2000},(_,i) => Math.sin(i/10)),Float32Array.from({length:2000},(_,i) => -Math.sin(i/10))];
  const before = data.map(x => x.slice());
  const buffer = {sampleRate:1000,length:2000,numberOfChannels:2,getChannelData:i => data[i]};
  smoothLoop(buffer,{loopStart:.4,loopEnd:1.4});
  assert.equal(data[0][1399],before[0][399]);
  assert.deepEqual(data[0].slice(0,1380),before[0].slice(0,1380));
  assert.deepEqual(data[0].slice(1400),before[0].slice(1400));
  assert.equal(data[0][1390],-data[1][1390]);
});
