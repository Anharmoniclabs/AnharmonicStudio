(function (root) {
  'use strict';

  // Real-time PCM capture for the web studio. Input audio is copied block by
  // block straight from the Web Audio graph, so a take is uncompressed, keeps
  // the device sample rate, and knows the audio-clock time of its first frame.
  // A MediaRecorder would compress the take and only report wall-clock time.
  const finite = (value, fallback = 0) => Number.isFinite(value) ? value : fallback;
  const MAX_CHANNELS = 2;

  // Pure accumulator: testable without an AudioContext.
  class PCMCapture {
    constructor(sampleRate, { maxFrames = Infinity } = {}) {
      this.sampleRate = Math.max(1, finite(sampleRate, 48000));
      this.maxFrames = Math.max(1, finite(maxFrames, Infinity));
      this.chunks = []; this.frames = 0; this.channels = 0; this.startTime = null; this.peak = 0; this.recentPeak = 0; this.full = false;
    }
    // `time` is the audio-clock time of the first frame in `channels`.
    push(channels, time) {
      if (this.full || !channels?.length || !channels[0]?.length) return false;
      const data = channels.slice(0, MAX_CHANNELS);
      if (this.startTime === null) { this.startTime = finite(time, 0); this.channels = data.length; }
      const room = this.maxFrames - this.frames;
      const kept = data.map(channel => room < channel.length ? channel.subarray(0, room) : channel);
      let peak = 0;
      for (const channel of kept) for (let index = 0; index < channel.length; index += 1) { const magnitude = Math.abs(channel[index]); if (magnitude > peak) peak = magnitude; }
      this.recentPeak = peak; if (peak > this.peak) this.peak = peak;
      this.chunks.push(kept); this.frames += kept[0].length;
      if (this.frames >= this.maxFrames) this.full = true;
      return true;
    }
    get duration() { return this.frames / this.sampleRate; }
    // Returns interleaved-free channel arrays, optionally dropping `trimSeconds` from the head.
    channelData(trimSeconds = 0) {
      const trim = Math.min(this.frames, Math.max(0, Math.round(finite(trimSeconds) * this.sampleRate)));
      const length = this.frames - trim;
      const output = Array.from({ length: Math.max(1, this.channels) }, () => new Float32Array(length));
      let offset = 0;
      for (const chunk of this.chunks) {
        const size = chunk[0].length;
        for (let channel = 0; channel < output.length; channel += 1) {
          const source = chunk[Math.min(channel, chunk.length - 1)];
          const from = Math.max(0, trim - offset);
          if (from < size) output[channel].set(from ? source.subarray(from) : source, offset + from - trim);
        }
        offset += size;
      }
      return output;
    }
    toAudioBuffer(context, trimSeconds = 0) {
      const data = this.channelData(trimSeconds);
      if (!data[0].length) throw new Error('The recording is empty. Record a longer take.');
      const buffer = context.createBuffer(data.length, data[0].length, this.sampleRate);
      data.forEach((channel, index) => buffer.copyToChannel ? buffer.copyToChannel(channel, index) : buffer.getChannelData(index).set(channel));
      return buffer;
    }
  }

  // Wires a MediaStream into a PCMCapture using an AudioWorklet when the
  // browser has one, or a ScriptProcessor on older engines. Monitoring feeds
  // the input to the given destination (off by default: phones feed back).
  class PCMRecorder {
    constructor(context, stream, { maxSeconds = 300, monitor = false, monitorDestination = null, workletURL = 'recorder-worklet.js', onLevel = () => {} } = {}) {
      if (!context || typeof context.createMediaStreamSource !== 'function') throw new Error('Real-time recording needs Web Audio input support.');
      this.context = context; this.stream = stream; this.workletURL = workletURL; this.onLevel = onLevel;
      this.capture = new PCMCapture(context.sampleRate, { maxFrames: Math.floor(maxSeconds * context.sampleRate) });
      this.source = context.createMediaStreamSource(stream);
      this.monitorGain = context.createGain(); this.monitorGain.gain.value = monitor ? 1 : 0;
      this.source.connect(this.monitorGain); this.monitorGain.connect(monitorDestination || context.destination);
      this.node = null; this.silence = null; this.state = 'idle'; this.armed = false; this.onFull = () => {};
    }
    setMonitor(enabled) { this.monitorGain.gain.setTargetAtTime(enabled ? 1 : 0, this.context.currentTime, .01); }
    get startTime() { return this.capture.startTime; }
    get duration() { return this.capture.duration; }
    get peak() { return this.capture.recentPeak; }
    async prepare() {
      if (this.node) return this.node;
      const context = this.context;
      if (context.audioWorklet && typeof AudioWorkletNode === 'function') {
        try {
          await context.audioWorklet.addModule(this.workletURL);
          const node = new AudioWorkletNode(context, 'anharmonic-capture', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: MAX_CHANNELS, channelCountMode: 'clamped-max' });
          node.port.onmessage = event => this.receive(event.data.channels, event.data.time);
          this.node = node; this.kind = 'worklet';
        } catch { this.node = null; }
      }
      if (!this.node) {
        if (typeof context.createScriptProcessor !== 'function') throw new Error('This browser cannot capture audio input in real time.');
        const node = context.createScriptProcessor(4096, MAX_CHANNELS, 1);
        node.onaudioprocess = event => {
          const input = event.inputBuffer; const channels = [];
          for (let channel = 0; channel < Math.min(MAX_CHANNELS, input.numberOfChannels); channel += 1) channels.push(Float32Array.from(input.getChannelData(channel)));
          // playbackTime is the clock of the output block; input arrives one block earlier.
          this.receive(channels, finite(event.playbackTime, context.currentTime) - input.length / context.sampleRate);
        };
        this.node = node; this.kind = 'script-processor';
      }
      // Keep the node alive: a silent gain to the destination avoids garbage collection.
      this.silence = context.createGain(); this.silence.gain.value = 0;
      this.source.connect(this.node); this.node.connect(this.silence); this.silence.connect(context.destination);
      return this.node;
    }
    receive(channels, time) {
      if (!channels?.length) return;
      let peak = 0; for (const channel of channels) for (let index = 0; index < channel.length; index += 1) { const magnitude = Math.abs(channel[index]); if (magnitude > peak) peak = magnitude; }
      this.level = peak; this.onLevel(peak);
      if (!this.armed || this.state !== 'recording') return;
      const blockStart = finite(time, this.context.currentTime);
      if (this.armAt !== null && blockStart + channels[0].length / this.context.sampleRate <= this.armAt) return;
      if (this.armAt !== null && blockStart < this.armAt) {
        const skip = Math.min(channels[0].length, Math.round((this.armAt - blockStart) * this.context.sampleRate));
        channels = channels.map(channel => channel.subarray(skip)); if (!channels[0].length) return;
        this.capture.push(channels, this.armAt); this.armAt = null; return;
      }
      this.armAt = null;
      const wasFull = this.capture.full;
      this.capture.push(channels, blockStart);
      if (this.capture.full && !wasFull) this.onFull();
    }
    // `at` is an optional audio-clock time; earlier input is discarded so a
    // count-in never lands in the take.
    async start(at = null) {
      await this.prepare();
      this.armAt = at === null ? null : Math.max(this.context.currentTime, finite(at)); this.armed = true; this.state = 'recording';
    }
    stop() {
      if (this.state === 'recording') this.state = 'stopped';
      this.armed = false;
      return this.capture;
    }
    close() {
      this.stop();
      try { this.node?.port?.postMessage('stop'); } catch { /* script processor */ }
      for (const node of [this.source, this.node, this.silence, this.monitorGain]) { try { node?.disconnect(); } catch { /* already gone */ } }
      if (this.node && 'onaudioprocess' in this.node) this.node.onaudioprocess = null;
      this.stream?.getTracks?.().forEach(track => { try { track.stop(); } catch { /* already stopped */ } });
      this.state = 'closed';
    }
  }

  // Seconds by which captured audio trails what the performer heard: input
  // buffering plus output buffering. Browsers that report neither get zero.
  function roundTripLatency(context) {
    return Math.max(0, finite(context?.baseLatency) + finite(context?.outputLatency));
  }

  const api = { PCMCapture, PCMRecorder, roundTripLatency };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnharmonicRecorder = api;
})(typeof window !== 'undefined' ? window : globalThis);
