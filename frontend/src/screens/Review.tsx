import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Evidence, type Page, type Propagation, type Submission, type SubmissionSummary } from "../api";
import { useApp } from "../app-context";
import { BatchPicker, Counters, DemoBadge } from "../components/BatchBar";
import { FormSignature } from "../components/FormSignature";
import { FormViewer, type Box } from "../components/FormViewer";
import {
  FIELD_HE,
  LTR_FIELDS,
  STATUS_HE,
  STATUS_ICON,
  TABLE_FIELDS,
  isGreen,
  nextOpenField,
  showValue,
  signatureOf,
} from "../fields";
import { useData } from "../hooks";
import { FixError, PropagationBar, useFix } from "./fix";
import { WhyDialog } from "./WhyDialog";
import "./Review.css";

type Sel = { sid: string; field: string };

export function Review() {
  const { batchId } = useApp();
  if (!batchId) return <div className="page empty">אין עדיין אצוות. התחילי בקליטה.</div>;
  return <ReviewBatch key={batchId} batchId={batchId} />;
}

function ReviewBatch({ batchId }: { batchId: string }) {
  const batch = useData(() => api.batch(batchId), [batchId], 0);
  const tubes = useData(() => api.tubes(batchId), [batchId]);
  // #/review/<submission>/<field> opens on that field (links from elsewhere)
  const [sel, setSel] = useState<Sel | null>(() => {
    const [, sid, field] = window.location.hash.replace(/^#\/?/, "").split("/");
    return sid && field ? { sid, field } : null;
  });
  const [prop, setProp] = useState<Propagation | null>(null);
  const [rev, setRev] = useState(0); // reloads the panel after a save
  const tableRef = useRef<HTMLDivElement>(null);
  const subs = batch.data?.submissions ?? [];
  const processing = batch.data?.status === "processing";

  // poll while the batch is still being read
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(batch.refresh, 2000);
    return () => clearInterval(t);
  }, [processing, batch.refresh]);

  // start on the first field that needs an eye
  useEffect(() => {
    if (!sel && subs.length) setSel(nextOpenField(subs, null) ?? { sid: subs[0].id, field: "first_name" });
  }, [subs, sel]);

  const matched = useMemo(
    () => new Set((tubes.data?.matched ?? []).map((t) => t.submission?.id)),
    [tubes.data],
  );

  function onSaved(p: Propagation | null) {
    batch.refresh();
    setRev((r) => r + 1);
    if (p) setProp(p);
    else if (sel) setSel(nextOpenField(subs, sel));
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !(e.target as HTMLElement).closest("input,textarea")) {
      e.preventDefault();
      const n = nextOpenField(subs, sel);
      if (n) setSel(n);
    }
  }

  if (batch.error) return <div className="page empty">{batch.error}</div>;
  const b = batch.data;

  return (
    <div className="page review">
      <div className="page-head">
        <h1>בדיקה</h1>
        {b && <span className="muted">{b.name}</span>}
        <DemoBadge show={b?.demo} />
        {processing && <span className="badge st-likely">בקריאה…</span>}
        <span className="grow" />
        <BatchPicker />
      </div>
      <Counters counts={b?.counts} />
      <div className="review-body">
        <div className="table-wrap card" ref={tableRef} tabIndex={0} onKeyDown={onKey} aria-label="טבלת הטפסים. Enter עובר לשדה הבא שצריך עין">
          <table className="forms">
            <thead>
              <tr>
                <th>שולח</th>
                <th aria-label="חותם הטופס" />
                {TABLE_FIELDS.map((f) => (
                  <th key={f}>{FIELD_HE[f]}</th>
                ))}
                <th>מבחנה</th>
              </tr>
            </thead>
            <tbody>
              {subs.map((s) => (
                <Row key={s.id} s={s} sel={sel} onSelect={setSel} tube={matched.has(s.id)} />
              ))}
            </tbody>
          </table>
          {!subs.length && <div className="empty">{processing ? "הטפסים נקראים…" : "אין טפסים באצווה"}</div>}
        </div>
        {sel && <Panel key={`${sel.sid}|${sel.field}|${rev}`} sel={sel} onSaved={onSaved} />}
      </div>
      {prop && (
        <PropagationBar
          batchId={batchId}
          prop={prop}
          onDone={() => {
            setProp(null);
            batch.refresh();
            setSel((cur) => nextOpenField(subs, cur));
            tableRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}

function Row({ s, sel, onSelect, tube }: { s: SubmissionSummary; sel: Sel | null; onSelect: (x: Sel) => void; tube: boolean }) {
  return (
    <tr className={sel?.sid === s.id ? "row-sel" : undefined}>
      <td>
        <Cell s={s} field="sender" sel={sel} onSelect={onSelect} />
      </td>
      <td className="sig-cell">
        <FormSignature statuses={signatureOf(s.fields)} tube={tube} />
      </td>
      {TABLE_FIELDS.map((f) => (
        <td key={f}>
          <Cell s={s} field={f} sel={sel} onSelect={onSelect} />
        </td>
      ))}
      <td className={`tube ${tube ? "yes" : ""}`} aria-label={tube ? "מבחנה הותאמה" : "אין מבחנה"}>
        {tube ? "✓" : "—"}
      </td>
    </tr>
  );
}

function Cell({ s, field, sel, onSelect }: { s: SubmissionSummary; field: string; sel: Sel | null; onSelect: (x: Sel) => void }) {
  const fr = s.fields[field];
  if (!fr) return null;
  const active = sel?.sid === s.id && sel.field === field;
  const text = fr.values.map((v) => showValue(field, v, true)).join(", ");
  return (
    <button
      className={`cell st-${fr.status}${active ? " active" : ""}`}
      onClick={() => onSelect({ sid: s.id, field })}
      aria-pressed={active}
      aria-label={`${FIELD_HE[field]}: ${text || "חסר"}, ${STATUS_HE[fr.status]}`}
    >
      <span className="cell-icon" aria-hidden="true">
        {STATUS_ICON[fr.status]}
      </span>
      {text ? (
        LTR_FIELDS.has(field) ? (
          <span className="num" dir="ltr">
            {text}
          </span>
        ) : (
          <span className="cell-text">{text}</span>
        )
      ) : (
        <span className="muted">—</span>
      )}
    </button>
  );
}

function Panel({ sel, onSaved }: { sel: Sel; onSaved: (p: Propagation | null) => void }) {
  const [sub, setSub] = useState<Submission | null>(null);
  const [ev, setEv] = useState<Evidence | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [glow, setGlow] = useState(false);
  const [why, setWhy] = useState(false);
  const [text, setText] = useState("");
  const fix = useFix(onSaved);

  useEffect(() => {
    let alive = true;
    api.submission(sel.sid).then((s) => {
      if (!alive) return;
      setSub(s);
      api.page(s.page_ids[0]).then((p) => alive && setPage(p)).catch(() => alive && setPage(null));
    });
    api.evidence(sel.sid, sel.field).then((e) => alive && setEv(e));
    return () => void (alive = false);
  }, [sel.sid, sel.field]);

  const fr = sub?.fields[sel.field];
  const options = ev?.options ?? [];

  // keys 1/2/3 pick a value, unless typing
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement).closest("input,textarea,select,dialog")) return;
      const i = Number(e.key) - 1;
      if (fr && i >= 0 && i < Math.min(3, options.length)) {
        e.preventDefault();
        fix.choose(sel.sid, sel.field, options[i].value, fr.values);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fr, options, sel, fix]);

  const boxes: Box[] = useMemo(() => {
    if (!sub) return [];
    return Object.values(sub.fields).flatMap((f) => {
      const b = f.sources.find((x) => x.bbox)?.bbox;
      return b ? [{ field: f.field, bbox: b, status: f.status }] : [];
    });
  }, [sub]);

  const ltr = LTR_FIELDS.has(sel.field);
  return (
    <aside className="panel card" aria-label={`פרטי השדה ${FIELD_HE[sel.field]}`}>
      <div className="panel-head">
        <h2>{FIELD_HE[sel.field]}</h2>
        {fr && (
          <span className={`badge st-${fr.status}`}>
            {STATUS_ICON[fr.status]} {STATUS_HE[fr.status]}
          </span>
        )}
        <span className="grow" />
        <label className="glow-toggle">
          <input type="checkbox" checked={glow} onChange={(e) => setGlow(e.target.checked)} /> הילת ביטחון
        </label>
      </div>
      {page?.image && sub ? (
        <FormViewer
          pageId={page.id}
          pageW={page.width}
          pageH={page.height}
          boxes={boxes}
          glow={glow}
          focus={ev?.crop ? { bbox: ev.crop.bbox, label: FIELD_HE[sel.field] } : null}
        />
      ) : (
        <div className="viewer placeholder muted">{sub ? "אין תמונה לעמוד" : "טוען…"}</div>
      )}
      {fr && (
        <div className="panel-body">
          <p className="reason">{fr.explanation_he || STATUS_HE[fr.status]}</p>
          <div className="options">
            {options.slice(0, 3).map((o, i) => (
              <button
                key={o.value}
                className={`option${fr.values.includes(o.value) ? " current" : ""}`}
                onClick={() => fix.choose(sel.sid, sel.field, o.value, fr.values)}
                disabled={fix.busy}
              >
                <kbd className="num">{i + 1}</kbd>
                {ltr ? (
                  <span className="num opt-v" dir="ltr">
                    {showValue(sel.field, o.value)}
                  </span>
                ) : (
                  <span className="opt-v">{o.value}</span>
                )}
                <span className="opt-src muted">{o.readers.join(" · ") || "הערך הנוכחי"}</span>
              </button>
            ))}
          </div>
          <form
            className="manual"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) fix.run(sel.sid, sel.field, text.trim());
            }}
          >
            <input
              className="input"
              dir={ltr ? "ltr" : "rtl"}
              placeholder="ערך אחר…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label={`ערך ידני ל${FIELD_HE[sel.field]}`}
            />
            <button className="btn" disabled={!text.trim() || fix.busy}>
              שמירה
            </button>
            {!isGreen(fr.status) && fr.values.length > 0 && (
              <button type="button" className="btn primary" onClick={() => fix.run(sel.sid, sel.field, null)} disabled={fix.busy}>
                אישור הערך
              </button>
            )}
          </form>
          <FixError error={fix.error} />
          <button className="link" onClick={() => setWhy(true)} disabled={!ev}>
            למה?
          </button>
        </div>
      )}
      {why && ev && <WhyDialog ev={ev} onClose={() => setWhy(false)} />}
    </aside>
  );
}
