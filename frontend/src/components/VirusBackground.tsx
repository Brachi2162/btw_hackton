import { Virus } from "./Virus";
import "./VirusBackground.css";

// Fixed layout so it never jumps between renders: x%, y%, size, opacity,
// drift duration (s), delay (s), spikes.
const VIRUSES: [number, number, number, number, number, number, number][] = [
  [6, 12, 70, 0.12, 38, 0, 10],
  [82, 8, 46, 0.09, 44, -6, 8],
  [64, 34, 92, 0.08, 52, -12, 12],
  [18, 58, 54, 0.14, 41, -3, 9],
  [88, 62, 64, 0.1, 47, -18, 11],
  [42, 80, 40, 0.16, 36, -9, 8],
  [30, 22, 34, 0.12, 33, -21, 7],
  [72, 86, 78, 0.09, 49, -14, 10],
  [4, 88, 44, 0.11, 40, -27, 9],
];

/** Faint viruses drifting slowly. Intake screen and card mode only. */
export function VirusBackground({ dark = false }: { dark?: boolean }) {
  return (
    <div className={`virus-bg${dark ? " dark" : ""}`} aria-hidden="true">
      {VIRUSES.map(([x, y, s, o, dur, delay, spikes], i) => (
        <div
          key={i}
          className="virus-float"
          style={{
            insetInlineStart: `${x}%`,
            top: `${y}%`,
            opacity: o,
            animationDuration: `${dur}s`,
            animationDelay: `${delay}s`,
          }}
        >
          <Virus size={s} spikes={spikes} />
        </div>
      ))}
    </div>
  );
}
