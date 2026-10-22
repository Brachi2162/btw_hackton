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
