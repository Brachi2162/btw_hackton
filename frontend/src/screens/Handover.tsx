import { useState } from "react";
import { api } from "../api";
import { useApp } from "../app-context";
import { DemoBadge } from "../components/BatchBar";
import { FormSignature } from "../components/FormSignature";
import { useToast } from "../components/Toast";
import { useData } from "../hooks";
import "./Handover.css";

const time = (iso: string) => new Date(iso).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });

/** End of shift: what was done, what passes to you, a note, and an ack. */
export function Handover() {
  const { go } = useApp();
  const toast = useToast();
  const h = useData(() => api.handover(), []);
  const [note, setNote] = useState("");
  const d = h.data;
  if (!d) return <div className="page empty">טוען…</div>;
  const p = d.pending;

  async function save() {
    await api.note(note);
    setNote("");
    toast("ההערה נשמרה ומחכה למשמרת הבאה");
    h.refresh();
  }
  async function ack(id: number) {
    await api.ack(id);
    toast("קיבלת את המשמרת");
    h.refresh();
  }

  return (
    <div className="page handover">
      <div className="page-head">
        <h1>העברת משמרת</h1>
        <span className="muted">
          מאז <span className="num">{time(d.since)}</span>
        </span>
        <DemoBadge show={p.has_demo} />
      </div>

      {d.open_handover && (
        <section className="card incoming">
          <div>
            <h2>הערה מ{d.open_handover.author}</h2>
            <p>{d.open_handover.text}</p>
            <span className="muted num">{time(d.open_handover.at)}</span>
          </div>
          <button className="btn primary" onClick={() => ack(d.open_handover!.id)}>
            קיבלתי את המשמרת
          </button>
        </section>
      )}

      <div className="ho-grid">
        <section className="card ho-block">
          <h2>מה נעשה</h2>
          <dl>
            <dt>עמודים נקראו</dt>
            <dd className="num">{d.done.pages_read}</dd>
            <dt>שדות תוקנו או אושרו</dt>
            <dd className="num">{d.done.fields_fixed}</dd>
            <dt>אצוות יוצאו</dt>
            <dd className="num">{d.done.batches_exported}</dd>
          </dl>
        </section>

        <section className="card ho-block">
          <h2>מה עובר אלייך</h2>
          <ul className="todo">
            <li>
              <button className="link" onClick={() => go("review")}>
                <span className="num">{p.review_waiting}</span> שדות מחכים לבדיקה
              </button>
            </li>
            <li>
              <button className="link" onClick={() => go("followups")}>
                <span className="num">{p.open_followups}</span> בירורים פתוחים
              </button>
              {p.overdue_followups > 0 && (
                <span className="late">
                  {" "}
                  (<span className="num">{p.overdue_followups}</span> יותר מיממה)
                </span>
              )}
            </li>
            <li>
              <button className="link" onClick={() => go("export")}>
                <span className="num">{p.batches_not_exported}</span> אצוות לא יוצאו
              </button>
            </li>
          </ul>
          {d.hot_forms.length > 0 && (
            <>
              <h3 className="hot-title">טפסים עם שדות כתומים</h3>
              <ul className="hot">
                {d.hot_forms.map((f) => (
                  <li key={f.id}>
                    <FormSignature statuses={f.signature.map((s) => s.status)} size={22} />
                    <span>{f.name || "ללא שם"}</span>
                    <span className="muted">{f.sender}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="card ho-block">
          <h2>הערה לצוות</h2>
          <textarea className="input" rows={5} value={note} onChange={(e) => setNote(e.target.value)} placeholder="מה חשוב שהמשמרת הבאה תדע?" aria-label="הערה לצוות" />
          <button className="btn primary" onClick={save} disabled={!note.trim()}>
            לסיים משמרת ולהשאיר הערה
          </button>
          {d.notes.length > 0 && (
            <ul className="notes">
              {d.notes.slice(0, 5).map((n) => (
                <li key={n.id}>
                  <strong>{n.author}</strong> <span className="muted num">{time(n.at)}</span>
                  <p>{n.text}</p>
                  {n.acked_by && <span className="muted small">✓ התקבלה על ידי {n.acked_by}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
