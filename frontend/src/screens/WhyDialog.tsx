import { useEffect, useRef } from "react";
import { imageUrl, type Evidence } from "../api";
import { LTR_FIELDS, showValue } from "../fields";
import "./WhyDialog.css";

/** "Why?": the evidence chain of one field in four numbered steps. */
export function WhyDialog({ ev, onClose }: { ev: Evidence; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal?.();
    return () => d?.close?.();
  }, []);
  const ltr = LTR_FIELDS.has(ev.field);
  const V = ({ v }: { v: string }) =>
    ltr ? (
      <span className="num" dir="ltr">
        {showValue(ev.field, v)}
      </span>
    ) : (
      <>{v}</>
    );

  return (
    <dialog ref={ref} className="why" onClose={onClose} onCancel={onClose} aria-labelledby="why-title">
      <header>
        <h2 id="why-title">למה? {ev.field_he}</h2>
        <button className="btn ghost" onClick={onClose} aria-label="סגירה">
          ✕
        </button>
      </header>
      <ol className="why-steps">
        <li>
          <h3>מה כתוב בטופס</h3>
          {ev.crop ? (
            <img
              className="why-crop"
              src={imageUrl(ev.crop.page_id, { crop: ev.crop.bbox, scale: 2, maxSide: 1200 })}
              alt={`חיתוך של ${ev.field_he} מהטופס`}
            />
          ) : (
            <p className="muted">אין מיקום בעמוד לשדה הזה.</p>
          )}
          {ev.crop?.approx && <p className="muted small">המיקום משוער.</p>}
        </li>
        <li>
          <h3>מה כל מקור קרא</h3>
          {ev.sources.length ? (
            <table className="why-table">
              <thead>
                <tr>
                  <th>מקור</th>
                  <th>ערך</th>
                  <th>ציטוט מהטופס</th>
                </tr>
              </thead>
              <tbody>
                {ev.sources.map((s, i) => (
                  <tr key={i}>
                    <td>
                      {s.reader_he}
                      {s.model && <div className="muted small num">{s.model}</div>}
                    </td>
                    <td>
                      {s.values.map((v) => (
                        <V key={v} v={v} />
                      ))}
                    </td>
                    <td className="quote">{s.quote ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">אף מקור לא מצא את השדה.</p>
          )}
        </li>
        <li>
          <h3>הבדיקות</h3>
          <ul className="why-checks">
            {ev.checks.map((c) => (
              <li key={c.label} className={c.ok ? "ok" : "bad"}>
                <span className="mark" aria-hidden="true">
                  {c.ok ? "✓" : "✗"}
                </span>
                <span>
                  {c.label}
                  {c.detail && <span className="muted"> · {c.detail}</span>}
                </span>
                <span className="sr-only">{c.ok ? "עבר" : "נכשל"}</span>
              </li>
            ))}
          </ul>
        </li>
        <li>
          <h3>ההחלטה</h3>
          <p className={`decision st-${ev.status}`}>{ev.decision_he}</p>
        </li>
      </ol>
    </dialog>
  );
}
