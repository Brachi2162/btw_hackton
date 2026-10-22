import { useEffect, useState } from "react";
import { api, imageUrl, type Evidence, type Propagation } from "../api";
import { useApp } from "../app-context";
import { VirusBackground } from "../components/VirusBackground";
import { FIELD_HE, LTR_FIELDS, STATUS_HE, showValue } from "../fields";
import { useData } from "../hooks";
import { FixError, PropagationBar, useFix } from "./fix";
import "./CardMode.css";

type Item = { submission_id: string; page_ids: string[]; field: string; status: keyof typeof STATUS_HE; values: string[]; explanation_he: string };

/** One field per card, for a fast pass over everything that needs an eye. */
export function CardMode() {
  const { batchId, go } = useApp();
  const queue = useData(() => (batchId ? api.reviewQueue(batchId) : Promise.resolve([])), [batchId]);
  const [total, setTotal] = useState(0);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [prop, setProp] = useState<Propagation | null>(null);

  useEffect(() => {
    if (queue.data && !total) setTotal(queue.data.length);
  }, [queue.data, total]);

  const items = (queue.data ?? []).filter((i) => !skipped.has(`${i.submission_id}|${i.field}`)) as Item[];
  const cur = items[0];
  const done = Math.max(0, total - items.length);

  const after = (p: Propagation | null) => {
    if (p) setProp(p);
    queue.refresh();
  };

  return (
    <div className="cards-mode">
      <VirusBackground dark />
      <div className="cards-top">
        <h1>כרטיסיות</h1>
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
          <span style={{ width: total ? `${(done / total) * 100}%` : "0%" }} />
        </div>
        <span className="num">
          {done}/{total}
        </span>
      </div>
      <div className="stack">
        {cur ? (
          <>
            {items[2] && <div className="ghost-card g2" aria-hidden="true" />}
            {items[1] && <div className="ghost-card g1" aria-hidden="true" />}
            <FieldCard
              key={`${cur.submission_id}|${cur.field}`}
              item={cur}
              onSaved={after}
              onSkip={() => setSkipped((s) => new Set(s).add(`${cur.submission_id}|${cur.field}`))}
            />
          </>
        ) : queue.data ? (
          <div className="field-card done-card">
            <h2>אין עוד שדות לבדיקה</h2>
            <p className="muted">
              {skipped.size ? `דילגת על ${skipped.size} שדות. אפשר לחזור אליהם בטבלה.` : "כל השדות אושרו או תוקנו."}
            </p>
            <button className="btn primary" onClick={() => go("export")}>
              לייצוא
            </button>
          </div>
        ) : (
          <div className="field-card">טוען…</div>
        )}
      </div>
      {prop && batchId && (
        <PropagationBar
          batchId={batchId}
          prop={prop}
          onDone={() => {
            setProp(null);
            queue.refresh();
          }}
        />
      )}
    </div>
  );
}

function FieldCard({ item, onSaved, onSkip }: { item: Item; onSaved: (p: Propagation | null) => void; onSkip: () => void }) {
  const [ev, setEv] = useState<Evidence | null>(null);
  const [text, setText] = useState("");
  const fix = useFix(onSaved);
  const ltr = LTR_FIELDS.has(item.field);

  useEffect(() => {
    api.evidence(item.submission_id, item.field).then(setEv);
  }, [item.submission_id, item.field]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement).closest("input,textarea,select")) return;
      const o = ev?.options[Number(e.key) - 1];
      if (o && Number(e.key) <= 3) {
        e.preventDefault();
        fix.choose(item.submission_id, item.field, o.value, item.values);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ev, item, fix]);

  return (
    <div className="field-card">
      <div className="fc-head">
        <h2>{FIELD_HE[item.field]}</h2>
        <span className={`badge st-${item.status}`}>{STATUS_HE[item.status]}</span>
      </div>
      <div className="fc-crop">
        {ev?.crop ? (
          <img src={imageUrl(ev.crop.page_id, { crop: ev.crop.bbox, scale: 2.5, maxSide: 1400 })} alt={`${FIELD_HE[item.field]} מתוך הטופס`} />
        ) : ev && item.page_ids[0] ? (
          // nothing to crop (e.g. a missing field): show the whole form
          <img className="fc-page" src={imageUrl(item.page_ids[0], { maxSide: 900 })} alt="הטופס כולו" />
        ) : (
          <span className="muted">טוען…</span>
        )}
      </div>
      <p className="fc-reason">{item.explanation_he}</p>
      <div className="fc-options">
        {(ev?.options ?? []).slice(0, 3).map((o, i) => (
          <button key={o.value} className="fc-opt" onClick={() => fix.choose(item.submission_id, item.field, o.value, item.values)} disabled={fix.busy}>
            <kbd className="num">{i + 1}</kbd>
            {ltr ? (
              <span className="num" dir="ltr">
                {showValue(item.field, o.value)}
              </span>
            ) : (
              <span>{o.value}</span>
            )}
            <small>{o.readers.join(" · ")}</small>
          </button>
        ))}
      </div>
      <form
        className="fc-manual"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) fix.run(item.submission_id, item.field, text.trim());
        }}
      >
        <input className="input" dir={ltr ? "ltr" : "rtl"} value={text} onChange={(e) => setText(e.target.value)} placeholder="ערך אחר…" aria-label="ערך ידני" />
        <button className="btn" disabled={!text.trim() || fix.busy}>
          שמירה
        </button>
        <button type="button" className="btn ghost" onClick={onSkip}>
          דילוג
        </button>
      </form>
      <FixError error={fix.error} />
    </div>
  );
}
