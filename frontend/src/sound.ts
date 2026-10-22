import { useEffect, useRef } from "react";

// Short UI sounds, synthesized with Web Audio (no audio files to ship).
// "done": a rising three-note chime when a batch finishes reading.
// "reply": a soft two-note blip when VERA answers in the chat.

let ctx: AudioContext | null = null;

function audio(): AudioContext | null {
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx ??= new Ctor();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

function tone(ac: AudioContext, freq: number, at: number, len: number, vol: number) {
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = "sine";
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(vol, at + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + len);
  osc.connect(gain).connect(ac.destination);
  osc.start(at);
  osc.stop(at + len + 0.02);
}

export function playSound(kind: "done" | "reply") {
  const ac = audio();
  if (!ac) return;
  const t = ac.currentTime + 0.02;
  if (kind === "done") {
    [523.25, 659.25, 783.99].forEach((f, i) => tone(ac, f, t + i * 0.13, 0.45, 0.18));
  } else {
    tone(ac, 880, t, 0.18, 0.1);
    tone(ac, 1174.66, t + 0.09, 0.25, 0.1);
  }
}

const FINISHED = new Set(["done", "exported"]);

/** Plays the "done" chime when a batch finishes reading, not when a screen
 * opens on a batch that was already finished. `fresh`: the batch was just
 * started here, so finishing on its very first status still counts. */
export function useDoneChime(status: string | undefined, fresh = false) {
  const prev = useRef(status);
  useEffect(() => {
    const was = prev.current;
    prev.current = status;
    if (!status || !FINISHED.has(status) || status === was) return;
    if (was ? !FINISHED.has(was) : fresh) playSound("done");
  }, [status, fresh]);
}

// --- the welcome page: soft electronic notes as the virus turns ---

// A minor pentatonic over two octaves: any order of these sounds calm.
const SCALE = [220, 261.63, 293.66, 329.63, 392, 440, 523.25, 587.33, 659.25, 783.99];
let spinOut: AudioNode | null = null;
let lastSpin = 0;

/** A quiet bus for the spin notes: low-passed, with a soft feedback echo. */
function spinBus(ac: AudioContext): AudioNode {
  if (spinOut) return spinOut;
  const input = ac.createGain();
  input.gain.value = 0.5;
  const lp = ac.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 2400;
  const delay = ac.createDelay(1);
  delay.delayTime.value = 0.32;
  const feedback = ac.createGain();
  feedback.gain.value = 0.32;
  const wet = ac.createGain();
  wet.gain.value = 0.45;
  input.connect(lp).connect(ac.destination);
  lp.connect(delay).connect(feedback).connect(delay);
  delay.connect(wet).connect(ac.destination);
  spinOut = input;
  return input;
}

/** One soft note for one notch of the virus's turn. Silent until the page
 * has had a click (browsers block sound before that), and at most one note
 * per 150ms however fast the scroll turns it. */
export function playSpinNote(notch: number) {
  if (!ctx || ctx.state !== "running") return;
  const nowMs = performance.now();
  if (nowMs - lastSpin < 150) return;
  lastSpin = nowMs;
  const ac = ctx;
  const t = ac.currentTime + 0.01;
  // walk the scale in a slow wave, so the notes rise and fall with the turn
  const i = Math.round(((Math.sin(notch * 0.7) + 1) / 2) * (SCALE.length - 1));
  const f = SCALE[i];
  const out = spinBus(ac);
  const voice = (freq: number, type: OscillatorType, vol: number, len: number) => {
    const osc = ac.createOscillator();
    const g = ac.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    osc.connect(g).connect(out);
    osc.start(t);
    osc.stop(t + len + 0.05);
  };
  voice(f, "sine", 0.07, 1.4);
  voice(f * 2, "triangle", 0.018, 0.6); // a little glassy shimmer
  voice(f * 1.005, "sine", 0.03, 1.4); // slight detune: the electronic chorus
}

/** Wakes the audio on the page's first click or key, so the spin notes
 * can start. */
export function unlockAudio() {
  audio();
}
