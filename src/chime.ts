// The Lurkbox notification sound: two soft notes, synthesized (no sound file).

let ctx: AudioContext | null = null;

export async function playChime() {
  try {
    ctx = ctx ?? new AudioContext();
    if (ctx.state !== "running") await ctx.resume();
    const t0 = ctx.currentTime + 0.02;
    for (const [hz, at, len, gain] of [[587.33, 0, 0.28, 0.16], [880, 0.13, 0.5, 0.14]]) {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      g.gain.setValueAtTime(0, t0 + at);
      g.gain.linearRampToValueAtTime(gain, t0 + at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + len);
      osc.connect(g).connect(ctx.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + len + 0.02);
    }
  } catch (e) {
    console.error("[Lurkbox] notification sound failed", e);
  }
}

export function closeChime() {
  void ctx?.close().catch(() => { /* already closed */ });
  ctx = null;
}
