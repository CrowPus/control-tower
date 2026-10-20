/** Microphone capture and PCM playback for talking to Nia (copied from the member app, src/features/assistant/audio.ts). */
export class AssistantAudio {
  private context: AudioContext;
  private stream: MediaStream | null = null;
  private capture: AudioWorkletNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextPlay = 0;
  private closed = false;
  private responsePending = false;
  private playbackStarted = false;
  private firstPlay = 0;
  private animationFrame = 0;
  private speaking = false;
  private analyser: AnalyserNode;
  private samples: Float32Array<ArrayBuffer>;
  muted = false;
  constructor(private onSpeaking: (active: boolean) => void, private onLevel: (level: number) => void) {
    this.context = new AudioContext();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    this.samples = new Float32Array(this.analyser.fftSize);
    this.analyser.connect(this.context.destination);
    this.context.onstatechange = () => this.updateSpeaking();
  }
  async start(onFrame: (pcm: ArrayBuffer) => void) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access requires a secure browser connection. You can still type.');
    await this.context.resume();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    if (this.closed) { stream.getTracks().forEach(t => t.stop()); throw new Error('Conversation canceled.'); }
    this.stream = stream;
    await this.context.audioWorklet.addModule('/assistant-capture.js');
    if (this.closed) throw new Error('Conversation canceled.');
    this.capture = new AudioWorkletNode(this.context, 'assistant-capture');
    const source = this.context.createMediaStreamSource(stream);
    const silent = this.context.createGain(); silent.gain.value = 0;
    this.capture.port.onmessage = ({ data }: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      if (!this.closed && !this.muted) { onFrame(data.pcm); if (!this.sources.size && !this.responsePending) this.onLevel(Math.min(1, data.level * 7)); }
    };
    source.connect(this.capture); this.capture.connect(silent); silent.connect(this.context.destination);
  }
  play(pcm: ArrayBuffer) {
    if (this.closed || !pcm.byteLength || pcm.byteLength % 2) return;
    // Google streams speech faster than real time, so a whole answer is queued ahead;
    // only a context that has stopped advancing can grow the queue past one answer.
    if (this.context.state !== 'running') void this.context.resume().catch(() => {});
    if (this.nextPlay - this.context.currentTime > 180) throw new Error('Audio playback stalled. Please restart voice.');
    const view = new DataView(pcm); const count = pcm.byteLength / 2;
    const audio = this.context.createBuffer(1, count, 24000); const values = audio.getChannelData(0);
    for (let i = 0; i < count; i++) values[i] = view.getInt16(i * 2, true) / 32768;
    const node = this.context.createBufferSource(); node.buffer = audio; node.connect(this.analyser);
    this.sources.add(node);
    this.responsePending = true;
    this.nextPlay = Math.max(this.nextPlay, this.context.currentTime + .025);
    if (!this.firstPlay) this.firstPlay = this.nextPlay;
    node.onended = () => {
      node.disconnect(); this.sources.delete(node);
      this.updateSpeaking();
    };
    node.start(this.nextPlay); this.nextPlay += audio.duration;
    if (!this.animationFrame) this.measurePlayback();
  }
  /** Server completion closes the response, but queued PCM must finish first. */
  finishResponse() {
    this.responsePending = false;
    this.updateSpeaking();
  }
  private updateSpeaking() {
    const pending = this.sources.size > 0 || this.responsePending;
    const active = !this.closed && this.context.state === 'running' && this.playbackStarted && pending;
    if (active !== this.speaking) { this.speaking = active; this.onSpeaking(active); }
    if (!pending) {
      this.firstPlay = 0; this.playbackStarted = false;
      cancelAnimationFrame(this.animationFrame); this.animationFrame = 0;
      this.onLevel(0);
    }
  }
  private measurePlayback = () => {
    // Use the audio clock, not arrival time or text length, to start the character.
    this.animationFrame = 0;
    if (this.closed) return;
    if (this.firstPlay && this.context.currentTime >= this.firstPlay) this.playbackStarted = true;
    this.updateSpeaking();
    if (this.sources.size || this.responsePending) {
      this.analyser.getFloatTimeDomainData(this.samples);
      const energy = this.samples.reduce((sum, value) => sum + value * value, 0) / this.samples.length;
      this.onLevel(this.speaking ? Math.min(1, Math.sqrt(energy) * 5) : 0);
      this.animationFrame = requestAnimationFrame(this.measurePlayback);
    }
  };

  mute(value: boolean) {
    this.muted = value;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = !value; });
    this.onLevel(0);
  }
  stopOutput() {
    for (const node of this.sources) { node.onended = null; node.stop(); node.disconnect(); }
    this.sources.clear(); this.nextPlay = 0;
    this.responsePending = false; this.updateSpeaking();
  }
  close() {
    if (this.closed) return;
    this.closed = true; this.stopOutput(); this.capture?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    this.context.onstatechange = null; this.analyser.disconnect();
    void this.context.close().catch(() => {});
  }
}
