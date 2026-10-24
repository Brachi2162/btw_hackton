import { useEffect, useMemo, useRef, useState } from "react";
import { api, imageUrl, type Evidence, type Page, type Propagation, type Submission, type SubmissionSummary } from "../api";
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
  nextAfterSave,
  nextOpenField,
  showValue,
  signatureOf,
} from "../fields";
import { useToast } from "../components/Toast";
import { useData } from "../hooks";
import { useDoneChime } from "../sound";
import { FixError, PropagationBar, useFix } from "./fix";
import { WhyDialog } from "./WhyDialog";
import "./Review.css";

type Sel = { sid: string; field: string };
type View = "zoom" | "page" | "mark";

/** A close-up around the field: at least ~900x360 page pixels, so the label
 * next to the value shows too, clamped to the page. */
function zoomBox(b: [number, number, number, number], w: number, h: number): number[] {
  const cx = (b[0] + b[2]) / 2;
  const cy = (b[1] + b[3]) / 2;
  const bw = Math.min(w, Math.max(900, (b[2] - b[0]) * 1.6));
  const bh = Math.min(h, Math.max(360, (b[3] - b[1]) * 1.6));
  const x1 = Math.max(0, Math.min(w - bw, cx - bw / 2));
  const y1 = Math.max(0, Math.min(h - bh, cy - bh / 2));
  return [x1, y1, x1 + bw, y1 + bh];
}

export function Review() {
  const { batchId } = useApp();
  if (!batchId) return <div className="page empty">אין עדיין אצוות. התחילי בקליטה.</div>;
  return <ReviewBatch key={batchId} batchId={batchId} />;
}

function ReviewBatch({ batchId }: { batchId: string }) {
  const batch = useData(() => api.batch(batchId), [batchId], 0);
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
  useDoneChime(batch.data?.status);

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

  function onSaved(p: Propagation | null) {
    batch.refresh();
    setRev((r) => r + 1);
    if (p) setProp(p);
    else if (sel) setSel(nextAfterSave(subs, sel)); // finish this form first
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
        {b && !processing && !b.locked && (
          <AcceptAll
            batchId={batchId}
            subs={subs}
            onDone={() => {
              batch.refresh();
              setRev((r) => r + 1);
            }}
          />
        )}
        <BatchPicker />
      </div>
      <Counters counts={b?.counts} />
      <div className="review-body">
        <div className="table-wrap card" ref={tableRef} tabIndex={0} onKeyDown={onKey} aria-label="טבלת הטפסים. Enter עובר לשדה הבא שצריך עין">
          <table className="forms">
            <colgroup>
              <col className="col-sender" />
              <col className="col-sig" />
              {TABLE_FIELDS.map((f) => (
                <col key={f} className={`col-${f}`} />
              ))}
            </colgroup>
            <thead>
              <tr>
                <th>שולח</th>
                <th aria-label="חותם הטופס" />
                {TABLE_FIELDS.map((f) => (
                  <th key={f}>{FIELD_HE[f]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {subs.map((s) => (
                <Row key={s.id} s={s} sel={sel} onSelect={setSel} />
              ))}
            </tbody>
          </table>
          {!subs.length && <div className="empty">{processing ? "הטפסים נקראים…" : "אין טפסים באצווה"}</div>}
        </div>
        {sel ? (
          <Panel key={`${sel.sid}|${sel.field}|${rev}`} sel={sel} onSaved={onSaved} />
        ) : (
          <aside className="panel card">
            <div className="viewer placeholder muted">
              <span>לחצי על שדה בטבלה כדי לראות את הטופס המקורי</span>
            </div>
          </aside>
        )}
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

/** Approve all: every field that has a value and is not green yet is
 * accepted as it is. Asks once before doing it; empty fields stay empty. */
function AcceptAll({ batchId, subs, onDone }: { batchId: string; subs: SubmissionSummary[]; onDone: () => void }) {
  const toast = useToast();
  const { refreshOverview } = useApp();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const count = subs.reduce(
    (n, s) =>
      n + Object.values(s.fields).filter((f) => f.values.length && (f.status === "likely" || f.status === "review")).length,
    0,
  );
  if (!count) return null;

  async function run() {
    setBusy(true);
    try {
      const r = await api.acceptAll(batchId);
      toast(
        r.left_empty
          ? `אושרו ${r.accepted} שדות. ${r.left_empty} שדות ריקים נשארו לבדיקה`
          : `אושרו ${r.accepted} שדות`,
      );
      refreshOverview();
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "האישור נכשל");
    } finally {
      setBusy(false);
      setAsking(false);
    }
  }

  return asking ? (
    <div className="accept-all ask" role="group" aria-label="אישור כל השדות">
      <span>
        לאשר <span className="num">{count}</span> שדות כמו שהם?
      </span>
      <button className="btn primary" onClick={run} disabled={busy} autoFocus>
        {busy ? "מאשרת…" : "כן, לאשר"}
      </button>
      <button className="btn" onClick={() => setAsking(false)} disabled={busy}>
        ביטול
      </button>
    </div>
  ) : (
    <button className="btn primary accept-all" onClick={() => setAsking(true)} title="מאשר כל שדה שיש בו ערך ועדיין לא ירוק. שדות ריקים לא משתנים">
      ✓ אישור הכל (<span className="num">{count}</span>)
    </button>
  );
}

function Row({ s, sel, onSelect }: { s: SubmissionSummary; sel: Sel | null; onSelect: (x: Sel) => void }) {
  return (
    <tr className={sel?.sid === s.id ? "row-sel" : undefined}>
      <td>
        <Cell s={s} field="sender" sel={sel} onSelect={onSelect} />
      </td>
      <td className="sig-cell">
        <FormSignature statuses={signatureOf(s.fields)} />
      </td>
      {TABLE_FIELDS.map((f) => (
        <td key={f}>
          <Cell s={s} field={f} sel={sel} onSelect={onSelect} />
        </td>
      ))}
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
      aria-label={`${FIELD_HE[field]}: ${text || (fr.guess ? `ניחוש ${fr.guess}` : "חסר")}, ${STATUS_HE[fr.status]}`}
    >
      <span className="cell-icon" aria-hidden="true">
        {STATUS_ICON[fr.status]}
      </span>
      {text ? (
        <Fit className={LTR_FIELDS.has(field) ? "num" : undefined} dir={LTR_FIELDS.has(field) ? "ltr" : undefined} text={text} />
      ) : fr.guess ? (
        // Read, but not surely (D-013): the guess, clearly marked as one; the
        // field itself stays empty (and empty in the export) until confirmed.
        <Fit
          className={`cell-guess${LTR_FIELDS.has(field) ? " num" : ""}`}
          dir="auto"
          title="ניחוש של המודל, לא נכתב"
          text={`${showValue(field, fr.guess, true)}?`}
        />
      ) : fr.status === "review" ? (
        <Fit className="muted" text="? לא נקרא בוודאות" />
      ) : (
        <span className="muted">—</span>
      )}
    </button>
  );
}

/** Cell text cut to the column width. When it does not fit, hovering or
 * focusing slides it to its end and back, so the whole value can be read
 * without widening the table. */
function Fit({ text, className, dir, title }: { text: string; className?: string; dir?: string; title?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  const start = () => {
    const el = ref.current;
    if (!el) return;
    const d = el.scrollWidth - el.clientWidth;
    if (d <= 1) return;
    setShift(getComputedStyle(el).direction === "rtl" ? d : -d);
  };
  const stop = () => setShift(0);
  return (
    <span
      ref={ref}
      className={`cell-text${shift ? " sliding" : ""}${className ? ` ${className}` : ""}`}
      dir={dir}
      title={title ?? text}
      onMouseEnter={start}
      onMouseLeave={stop}
      onTouchStart={start}
      style={
        shift
          ? ({ "--shift": `${shift}px`, "--dur": `${Math.max(1.6, Math.abs(shift) / 40)}s` } as React.CSSProperties)
          : undefined
      }
    >
      <span className="cell-inner">{text}</span>
    </span>
  );
}

function ViewerArea({
  page,
  ev,
  field,
  view,
  setView,
  boxes,
  glow,
  onPick,
}: {
  page: Page;
  ev: Evidence | null;
  field: string;
  view: View;
  setView: (v: View) => void;
  boxes: Box[];
  glow: boolean;
  onPick: (bbox: [number, number, number, number]) => void;
}) {
  const crop = ev?.crop && ev.crop.page_id === page.id ? ev.crop : null;
  const mode: View = view === "zoom" && !crop ? "page" : view;
  const note = !crop
    ? "אין מיקום לשדה הזה. אפשר לסמן אותו בעמוד."
    : crop.approx
        ? "האזור שבו המודל ראה את הערך. המיקום המדויק לא ידוע: אפשר לסמן אותו."
        : null;
  return (
    <div className="viewer-area">
      <div className="view-tabs" role="tablist" aria-label="תצוגת הטופס">
        <button role="tab" aria-selected={mode === "zoom"} className={mode === "zoom" ? "on" : ""} onClick={() => setView("zoom")} disabled={!crop}>
          תקריב
        </button>
        <button role="tab" aria-selected={mode === "page"} className={mode === "page" ? "on" : ""} onClick={() => setView("page")}>
          כל העמוד
        </button>
        <button role="tab" aria-selected={mode === "mark"} className={mode === "mark" ? "on mark" : "mark"} onClick={() => setView(mode === "mark" ? "zoom" : "mark")}>
          {mode === "mark" ? "ביטול סימון" : "⌖ סמני מיקום"}
        </button>
      </div>
      {mode === "mark" && <p className="view-note strong">גררי מלבן סביב המקום שבו כתוב {FIELD_HE[field]} בטופס.</p>}
      {mode === "zoom" && crop ? (
        <div className="zoom-view">
          <img
            src={imageUrl(crop.page_id, { crop: zoomBox(crop.bbox, page.width, page.height), maxSide: 1600 })}
            alt={`${FIELD_HE[field]} בתקריב מתוך הטופס`}
          />
        </div>
      ) : (
        <FormViewer
          pageId={page.id}
          pageW={page.width}
          pageH={page.height}
          boxes={boxes}
          glow={glow && mode !== "mark"}
          focus={mode === "page" && crop ? { bbox: crop.bbox, label: FIELD_HE[field], approx: crop.approx } : null}
          onPick={mode === "mark" ? onPick : undefined}
        />
      )}
      {note && mode !== "mark" && <p className="view-note muted small">{note}</p>}
    </div>
  );
}

function Panel({ sel, onSaved }: { sel: Sel; onSaved: (p: Propagation | null) => void }) {
  const [sub, setSub] = useState<Submission | null>(null);
  const [ev, setEv] = useState<Evidence | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [glow, setGlow] = useState(false);
  const [view, setView] = useState<View>("zoom");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const toast = useToast();
  const [why, setWhy] = useState(false);
  const [text, setText] = useState("");
  const fix = useFix(onSaved);

  useEffect(() => {
    let alive = true;
    // Never show one row's evidence over another row's page while loading.
    setSub(null);
    setEv(null);
    setPage(null);
    setLoadError(null);
    // A failed request says so (with a retry) instead of loading forever.
    const failed = (e: unknown) => alive && setLoadError(e instanceof Error ? e.message : String(e));
    api
      .submission(sel.sid)
      .then((s) => {
        if (!alive) return;
        setSub(s);
        if (s.page_ids[0]) {
          api.page(s.page_ids[0]).then((p) => alive && setPage(p)).catch(() => alive && setPage(null));
        }
      })
      .catch(failed);
    api.evidence(sel.sid, sel.field).then((e) => alive && setEv(e)).catch(failed);
    return () => void (alive = false);
  }, [sel.sid, sel.field, attempt]);

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
        <ViewerArea
          page={page}
          ev={ev && ev.submission_id === sel.sid && ev.field === sel.field ? ev : null}
          field={sel.field}
          view={view}
          setView={setView}
          boxes={boxes}
          glow={glow}
          onPick={async (bbox) => {
            try {
              setEv(await api.setLocation(sel.sid, sel.field, page.id, bbox));
              setView("zoom");
              toast("המיקום נשמר לטופס הזה");
            } catch (e) {
              toast(e instanceof Error ? e.message : "המיקום לא נשמר");
            }
          }}
        />
      ) : (
        <div className="viewer placeholder muted">
          {loadError ? (
            <span role="alert">
              הטופס לא נטען ({loadError}).{" "}
              <button className="link" onClick={() => setAttempt((n) => n + 1)}>
                נסי שוב
              </button>
            </span>
          ) : sub ? (
            "אין תמונה לעמוד"
          ) : (
            "טוען…"
          )}
        </div>
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
            {fr.status !== "missing" && (
              <button
                type="button"
                className="btn ghost no-value"
                onClick={() => fix.run(sel.sid, sel.field, "")}
                disabled={fix.busy}
                title="אין את הערך בטופס: השדה יישאר ריק, ובסוף הבדיקה ייפתח בירור עם השולח"
              >
                אין בטופס – לבירור
              </button>
            )}
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
