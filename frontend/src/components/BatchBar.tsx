import { api, type Counts } from "../api";
import { useApp } from "../app-context";
import { useData } from "../hooks";
import "./BatchBar.css";

export function DemoBadge({ show }: { show: boolean | undefined }) {
  return show ? <span className="badge demo">נתוני דוגמה</span> : null;
}

/** Choose which batch the screens work on (default: the latest). */
export function BatchPicker() {
  const { batchId, setBatchId } = useApp();
  const { data } = useData(() => api.batches(), [batchId]);
  if (!data?.length) return null;
  return (
    <label className="batch-picker">
      <span className="muted">אצווה</span>
      <select className="input" value={batchId ?? ""} onChange={(e) => setBatchId(e.target.value)}>
        {data.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
            {b.locked ? " (יוצאה)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

const COUNTERS: { key: string; label: string; cls: string }[] = [
  { key: "verified", label: "מאומתים", cls: "verified" },
  { key: "likely", label: "סבירים", cls: "likely" },
  { key: "review", label: "לבדיקה", cls: "review" },
  { key: "missing", label: "חסרים", cls: "missing" },
];

/** Field counters: verified (incl. human), likely, review, missing. */
export function Counters({ counts }: { counts: Counts | undefined }) {
  const f = counts?.fields ?? {};
  return (
    <div className="counters">
      {COUNTERS.map((c) => {
        const n = (f[c.key] ?? 0) + (c.key === "verified" ? (f.human ?? 0) : 0);
        return (
          <div key={c.key} className={`counter c-${c.cls}`}>
            <span className="counter-n num">{n}</span>
            <span className="counter-l">{c.label}</span>
          </div>
        );
      })}
    </div>
  );
}
