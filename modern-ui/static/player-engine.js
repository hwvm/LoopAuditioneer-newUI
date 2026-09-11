'use strict';
// Kept independent of the DOM so MIDI ownership and voice lifetimes are testable.
(function (root) {
  class OrganPlayer {
    constructor(context, changed = () => {}) {
      this.context = context;
      this.changed = changed;
      this.stops = new Map();
      this.held = new Map();
      this.pedals = new Set();
      this.voices = new Set();
      this.release = .35;
      this.extension = 0;
      this.maxVoices = 192;
      this.master = context.createGain();
      this.master.gain.value = .28;
      this.compressor = context.createDynamicsCompressor();
      this.compressor.threshold.value = -8;
      this.compressor.knee.value = 8;
      this.compressor.ratio.value = 12;
      this.compressor.attack.value = .003;
      this.compressor.release.value = .15;
      this.master.connect(this.compressor).connect(context.destination);
    }
    addStop(stop, samples) {
      const gain = this.context.createGain();
      gain.gain.value = .7;
      gain.connect(this.master);
      this.stops.set(stop.id, {...stop, samples, gain, enabled: false});
    }
    destroy() {
      this.held.clear(); this.pedals.clear();
      for (const voice of [...this.voices]) this.dispose(voice);
      for (const stop of this.stops.values()) stop.gain.disconnect();
      this.master.disconnect(); this.compressor.disconnect();
    }
    enable(id, enabled) {
      const stop = this.stops.get(id);
      if (!stop || stop.enabled === enabled) return;
      stop.enabled = enabled;
      if (!enabled) {
        for (const voice of this.voices) if (voice.stopId === id) this.endVoice(voice, .025);
      } else {
        for (const held of this.held.values()) this.startVoice(stop, held);
      }
      this.changed();
    }
    sampleFor(stop, key) {
      const sample = stop.samples.reduce((best, item) => !best || Math.abs(item.key - key) < Math.abs(best.key - key) ? item : best, null);
      return sample && Math.abs(sample.key - key) <= this.extension ? sample : null;
    }
    startVoice(stop, held) {
      const sample = this.sampleFor(stop, held.note);
      if (!sample) return;
      if (this.voices.size >= this.maxVoices) this.dispose(this.voices.values().next().value);
      const source = this.context.createBufferSource(), gain = this.context.createGain();
      source.buffer = sample.buffer;
      source.loop = true;
      source.loopStart = sample.loopStart;
      source.loopEnd = sample.loopEnd;
      source.playbackRate.value = 2 ** ((held.note - sample.key) / 12);
      const now = this.context.currentTime;
      gain.gain.setValueAtTime(0, now);
      // Pipe stops have fixed wind pressure: velocity does not alter their level.
      gain.gain.linearRampToValueAtTime(sample.gain, now + .008);
      source.connect(gain).connect(stop.gain);
      const voice = {source, gain, owner: held.owner, note: held.note, stopId: stop.id, ending: false};
      source.onended = () => this.dispose(voice);
      this.voices.add(voice);
      source.start(now);
    }
    noteOn(note, velocity = 100, owner = 'screen') {
      if (!Number.isInteger(note) || note < 0 || note > 127) return;
      if (!velocity) return this.noteOff(note, owner);
      const id = `${owner}:${note}`;
      this.releaseNote(note, owner, .015);
      const held = {note, owner, down: true};
      this.held.set(id, held);
      for (const stop of this.stops.values()) if (stop.enabled) this.startVoice(stop, held);
      this.changed();
    }
    noteOff(note, owner = 'screen') {
      const held = this.held.get(`${owner}:${note}`);
      if (!held) return;
      held.down = false;
      if (!this.pedals.has(owner)) this.releaseNote(note, owner);
      this.changed();
    }
    releaseNote(note, owner, seconds = this.release) {
      this.held.delete(`${owner}:${note}`);
      for (const voice of this.voices) if (voice.owner === owner && voice.note === note) this.endVoice(voice, seconds);
    }
    endVoice(voice, seconds) {
      if (voice.ending) return;
      voice.ending = true;
      const now = this.context.currentTime;
      voice.gain.gain.cancelAndHoldAtTime(now);
      voice.gain.gain.linearRampToValueAtTime(0, now + seconds);
      voice.source.stop(now + seconds + .01);
    }
    dispose(voice) {
      if (!this.voices.delete(voice)) return;
      voice.source.onended = null;
      try { voice.source.stop(); } catch (_) { /* Already ended. */ }
      voice.source.disconnect();
      voice.gain.disconnect();
      this.changed();
    }
    sustain(owner, down) {
      if (down) this.pedals.add(owner);
      else {
        this.pedals.delete(owner);
        for (const held of [...this.held.values()]) if (held.owner === owner && !held.down) this.releaseNote(held.note, owner);
      }
      this.changed();
    }
    allOff(owner = null) {
      for (const held of [...this.held.values()]) if (owner === null || held.owner === owner) this.held.delete(`${held.owner}:${held.note}`);
      for (const voice of this.voices) if (owner === null || voice.owner === owner) this.endVoice(voice, .02);
      if (owner === null) this.pedals.clear(); else this.pedals.delete(owner);
      this.changed();
    }
    midi(data, device = 'midi') {
      if (!data || data.length < 3) return;
      const command = data[0] & 0xf0, channel = data[0] & 15, owner = `${device}/${channel}`;
      if (command === 0x90) this.noteOn(data[1], data[2], owner);
      if (command === 0x80) this.noteOff(data[1], owner);
      if (command === 0xb0) {
        if (data[1] === 64) this.sustain(owner, data[2] >= 64);
        if (data[1] === 120 || data[1] === 123) this.allOff(owner);
        if (data[1] === 121) this.sustain(owner, false);
      }
    }
  }

  function smoothLoop(buffer, sample) {
    const start = Math.round(sample.loopStart * buffer.sampleRate), end = Math.round(sample.loopEnd * buffer.sampleRate);
    const length = Math.min(Math.round(.02 * buffer.sampleRate), start, Math.floor((end - start) / 4));
    if (length < 2 || end > buffer.length) return;
    // Blend the material just before loop-start into the final 20 ms of the
    // loop. Only this decoded audition copy changes; source PCM stays intact.
    for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
      const data = buffer.getChannelData(channel);
      for (let i = 0; i < length; i++) {
        const mix = i / (length - 1);
        data[end - length + i] = data[end - length + i] * (1 - mix) + data[start - length + i] * mix;
      }
    }
  }
  if (typeof module !== 'undefined') module.exports = {OrganPlayer, smoothLoop};
  else Object.assign(root, {OrganPlayer, smoothLoop});
})(typeof window !== 'undefined' ? window : {});
