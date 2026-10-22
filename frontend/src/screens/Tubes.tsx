import { useEffect, useRef, useState } from "react";
import { api, type Brief, type TubeScan } from "../api";
import { useApp } from "../app-context";
import { BatchPicker, DemoBadge } from "../components/BatchBar";
import { FormSignature } from "../components/FormSignature";
import { useToast } from "../components/Toast";
import { useData } from "../hooks";
import "./Tubes.css";

const COLS = ["A", "B", "C", "D", "E", "F"];

export function Tubes() {
  const { batchId, refreshOverview } = useApp();
  const toast = useToast();
  const st = useData(() => (batchId ? api.tubes(batchId) : Promise.resolve(null)), [batchId]);
  const batch = useData(() => (batchId ? api.batch(batchId) : Promise.resolve(null)), [batchId]);
  const [value, setValue] = useState("");
  const [last, setLast] = useState<TubeScan | null>(null);
  const input = useRef<HTMLInputElement>(null);

  // The scanner types into this field: keep it focused.
  useEffect(() => {
    const keep = () => {
      const a = document.activeElement;
      if (!a || a === document.body) input.current?.focus();
    };
    const t = setInterval(keep, 400);
    input.current?.focus();
    return () => clearInterval(t);
  }, []);

  async function scan(e: React.FormEvent) {
    e.preventDefault();
    const n = value.trim();
    if (!n || !batchId) return;
    setValue("");
    try {
      const r = await api.scan(batchId, n);
      setLast(r);
      if (r.duplicate) toast(`מבחנה ${r.number} כבר בתא ${r.label}`);
      else if (!r.matched) toast(`אין טופס למבחנה ${r.number}. נפתח בירור`);
      st.refresh();
      refreshOverview();
    } catch (err) {
      toast(err instanceof Error ? err.message : "שגיאה בסריקה");
    }
    input.current?.focus();
  }

  if (!batchId) return <div className="page empty">אין עדיין אצוות.</div>;
  const data = st.data;
  const shown = last ?? data?.last ?? null;

  return (
    <div className="page tubes">
      <div className="page-head">
        <h1>מבחנות</h1>
        <DemoBadge show={batch.data?.demo} />
        <span className="grow" />
        <BatchPicker />
      </div>

      <div className="tubes-top">
        <form className="scan-panel" onSubmit={scan} onClick={() => input.current?.focus()}>
          <div className="laser" aria-hidden="true" />
          <label htmlFor="tube-in" className="scan-label">
            סרקי מבחנה
          </label>
          <input
            id="tube-in"
            ref={input}
            className="scan-input num"
            dir="ltr"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            placeholder="_ _ _ _ _ _"
          />
          <div className="scan-last" aria-live="polite">
            {shown ? (
              <>
                <span className="muted-light">נסרקה אחרונה</span>
                <span className="num big" dir="ltr">
                  {shown.number}
                </span>
                <span className={`scan-result ${shown.matched ? "ok" : "orphan"}`}>
                  {shown.matched ? `${shown.submission?.name || "טופס"} · ${shown.submission?.sender ?? ""}` : "אין טופס"}
                </span>
                <span className="num">
                  תא <span dir="ltr">{shown.label}</span>
                </span>
              </>
            ) : (
              <span className="muted-light">ממתינה לסריקה ראשונה</span>
            )}
          </div>
        </form>

        <div className="rack-wrap card">
          <div className="rack-head">
            <h2>
              מתקן <span className="num">{data?.rack ?? 1}</span>
            </h2>
            <span className="muted">מבט מלמעלה</span>
          </div>
          <div className="rack" dir="ltr" role="grid" aria-label="מתקן מבחנות 6 על 5">
            <div className="rack-corner" />
            {COLS.map((c) => (
              <div key={c} className="rack-col num">
                {c}
              </div>
            ))}
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="rack-row" role="row">
                <div className="rack-rowlabel num">{row + 1}</div>
                {COLS.map((_, col) => {
                  const s = data?.slots[row * 6 + col];
                  return (
                    <div
                      key={col}
                      role="gridcell"
                      className={`slot ${s?.state ?? "empty"}${s?.newest ? " newest" : ""}`}
                      aria-label={`${s?.label}: ${s?.state === "empty" ? "ריק" : s?.state === "matched" ? `מבחנה ${s.number}, הותאמה` : `מבחנה ${s?.number}, בלי טופס`}`}
                      title={s?.number ?? undefined}
                    >
                      {s?.state === "matched" && <span aria-hidden="true">✓</span>}
                      {s?.state === "orphan" && <span aria-hidden="true">!</span>}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="tube-lists">
        <List title="הותאמו" tone="ok" items={(data?.matched ?? []).map((t) => ({ key: t.id, number: t.number, label: t.label, brief: t.submission }))} />
        <List title="מבחנה בלי טופס" tone="orphan" items={(data?.orphans ?? []).map((t) => ({ key: t.id, number: t.number, label: t.label, brief: null }))} />
        <List title="טפסים שמחכים למבחנה" tone="wait" items={(data?.waiting ?? []).map((b) => ({ key: b.id, number: b.central, label: "", brief: b }))} />
      </div>
    </div>
  );
}

function List({
  title,
  tone,
  items,
}: {
  title: string;
  tone: string;
  items: { key: string | number; number: string; label: string; brief: Brief | null }[];
}) {
  return (
    <section className={`tube-list card ${tone}`}>
      <h2>
        {title} <span className="num muted">{items.length}</span>
      </h2>
      <ul>
        {items.slice().reverse().map((it) => (
          <li key={it.key}>
            {it.brief && <FormSignature statuses={it.brief.signature.map((s) => s.status)} tube={tone === "ok"} size={22} />}
            <span className="num" dir="ltr">
              {it.number || "—"}
            </span>
            <span className="grow">{it.brief ? `${it.brief.name} · ${it.brief.sender}` : ""}</span>
            {it.label && (
              <span className="num slot-tag" dir="ltr">
                {it.label}
              </span>
            )}
          </li>
        ))}
        {!items.length && <li className="muted">אין</li>}
      </ul>
    </section>
  );
}
