import type { Status } from "../api";
import { STATUS_HE } from "../fields";
import "./FormSignature.css";

const COLOR: Record<Status, string> = {
  verified: "var(--verified)",
  human: "var(--verified)",
  likely: "var(--likely)",
  review: "var(--review)",
  missing: "var(--missing)",
};
const NAMES = ["שם", "ת.ז.", "מספר מרכזי", "תאריך קבלה", "מספר התחייבות"];
const GAP = 14; // degrees between arcs

function arc(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const p = (a: number) => {
    const rad = ((a - 90) * Math.PI) / 180;
    return `${(cx + r * Math.cos(rad)).toFixed(2)} ${(cy + r * Math.sin(rad)).toFixed(2)}`;
  };
  return `M ${p(a0)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${p(a1)}`;
}

/** The form's signature: five arcs (name, ID, central number, receipt date,
 * commitment number) in their status colours, and a centre dot that is
 * filled when the tube has been matched. A `review` arc pulses slowly. */
export function FormSignature({
  statuses,
  tube,
  size = 30,
}: {
  statuses: Status[];
  tube?: boolean;
  size?: number;
}) {
  const r = 15;
  const step = 360 / statuses.length;
  const label =
    statuses.map((s, i) => `${NAMES[i]}: ${STATUS_HE[s]}`).join(", ") +
    (tube === undefined ? "" : tube ? ", מבחנה הותאמה" : ", אין מבחנה");
  return (
    <svg className="sig" viewBox="0 0 40 40" width={size} height={size} role="img" aria-label={label}>
      <title>{label}</title>
      {statuses.map((s, i) => (
        <path
          key={i}
          d={arc(20, 20, r, i * step + GAP / 2, (i + 1) * step - GAP / 2)}
          stroke={COLOR[s]}
          strokeWidth={5}
          strokeLinecap="round"
          fill="none"
          className={s === "review" ? "sig-pulse" : undefined}
          data-status={s}
        />
      ))}
      {tube === undefined ? null : tube ? (
        <circle cx={20} cy={20} r={5} fill="var(--verified)" data-tube="yes" />
      ) : (
        <circle cx={20} cy={20} r={4.2} fill="none" stroke="var(--missing)" strokeWidth={1.6} data-tube="no" />
      )}
    </svg>
  );
}
