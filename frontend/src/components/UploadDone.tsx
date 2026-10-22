import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Virus } from "./Virus";
import "./UploadDone.css";

type Done = { title: string; detail: string };

const Ctx = createContext<(done: Done) => void>(() => {});

/** Say "the files are in": the virus orb spins up and settles, a check
 * mark draws itself, and a soft two-note chime plays. Shown over whatever
 * screen is open, since an upload goes on after leaving the intake screen. */
export const useUploadDone = () => useContext(Ctx);

const SHOW_MS = 3200;

/** A short, quiet chime from the browser itself (no sound file): two sine
 * notes a fifth apart with a soft bell envelope. Silently skipped when the
 * browser has no audio or blocks it. */
function chime() {
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    const ctx = new AC();
    const master = ctx.createGain();
    master.gain.value = 0.12;
    master.connect(ctx.destination);
    const notes: [number, number][] = [
      [659.25, 0], // E5
      [987.77, 0.14], // B5
    ];
    for (const [freq, at] of notes) {
      const t = ctx.currentTime + at;
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(1, t + 0.015);
      env.gain.exponentialRampToValueAtTime(0.001, t + 0.9);
      osc.connect(env).connect(master);
      osc.start(t);
      osc.stop(t + 0.95);
    }
    window.setTimeout(() => ctx.close().catch(() => {}), 1400);
  } catch {
    /* no audio: the picture says it */
  }
}

export function UploadDoneProvider({ children }: { children: React.ReactNode }) {
  const [done, setDone] = useState<(Done & { id: number }) | null>(null);
  const timer = useRef<number>();
  const show = useCallback((d: Done) => {
    setDone((cur) => ({ ...d, id: (cur?.id ?? 0) + 1 }));
    chime();
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDone(null), SHOW_MS);
  }, []);
  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="upload-done-slot" role="status" aria-live="polite">
        {done && (
          <button
            type="button"
            className="upload-done"
            key={done.id}
            onClick={() => setDone(null)}
            aria-label={`${done.title}. ${done.detail}`}
          >
            <span className="ud-orb" aria-hidden="true">
              <span className="ud-ring" />
              <Virus size={54} spikes={9} className="ud-virus" />
              <svg className="ud-check" viewBox="0 0 52 52">
                <circle cx="26" cy="26" r="24" />
                <path d="M15 27 l7 7 l15 -16" />
              </svg>
            </span>
            <span className="ud-text">
              <strong>{done.title}</strong>
              <span>{done.detail}</span>
            </span>
          </button>
        )}
      </div>
    </Ctx.Provider>
  );
}
