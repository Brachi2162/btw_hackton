import { useEffect, useState } from "react";
import { api } from "../api";
import { DemoBadge } from "../components/BatchBar";
import { useToast } from "../components/Toast";
import { useData } from "../hooks";
import "./Weekly.css";

const d = (iso: string) => iso.split("-").reverse().slice(0, 2).join("/");

export function Weekly() {
  const { data: r, error } = useData(() => api.weekly(), []);
  if (error) return <div className="page empty">{error}</div>;
  if (!r) return <div className="page empty">טוען…</div>;
  const s = r.stats;
  const maxDay = Math.max(1, ...r.orphans_by_day.map((x) => x.count));

  return (
    <div className="page weekly">
      <div className="page-head">
        <h1>דוח שבועי</h1>
        <span className="muted num" dir="ltr">
          {d(r.from)} – {d(r.to)}
        </span>
        <DemoBadge show={r.has_demo} />
      </div>

      <div className="stats">
        <div className="stat dark">
          <span className="stat-n num">{s.hours_saved}</span>
          <span className="stat-l">שעות עבודה נחסכו</span>
          <span className="stat-sub">
            לפי <span className="num">{s.manual_seconds_per_form}</span> שניות הקלדה לטופס
          </span>
        </div>
        <div className="stat card">
          <span className="stat-n num">{s.forms}</span>
          <span className="stat-l">טפסים התקבלו</span>
        </div>
        <div className="stat card">
          <span className="stat-n num">{s.auto_approved_pct}%</span>
          <span className="stat-l">אושרו בלי מגע יד</span>
        </div>
        <div className="stat card">
          <span className="stat-n num">{s.tubes_without_form}</span>
          <span className="stat-l">מבחנות בלי טופס</span>
        </div>
      </div>

      <div className="charts">
        <section className="card chart">
          <h2>{r.takeaways.senders}</h2>
          <p className="muted chart-sub">אחוז השדות שהצריכו עין, לפי שולח</p>
          <ul className="bars">
            {r.by_sender.map((x) => (
              <li key={x.sender}>
                <span className="bar-name">{x.sender}</span>
                <span className="bar-track">
                  <span
                    className={`bar-fill${x.sender === r.worst_sender ? " worst" : ""}`}
                    style={{ width: `${Math.max(2, x.needs_eye_pct)}%` }}
                  />
                </span>
                <span className="bar-val num">{x.needs_eye_pct}%</span>
                <span className="bar-reason muted">{x.top_reason_he}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card chart">
          <h2>{r.takeaways.orphans}</h2>
          <p className="muted chart-sub">מבחנות שהגיעו בלי טופס, לפי יום</p>
          <div className="cols" role="img" aria-label={r.orphans_by_day.map((x) => `${x.day_he}: ${x.count}`).join(", ")}>
            {r.orphans_by_day.map((x) => (
              <div key={x.date} className="col">
                <span className="col-n num">{x.count || ""}</span>
                <span className="col-bar" style={{ height: `${(x.count / maxDay) * 100}%` }} />
                <span className="col-l">{x.day_he}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {r.worst_sender && <Letter sender={r.worst_sender} />}
    </div>
  );
}

function Letter({ sender }: { sender: string }) {
  const toast = useToast();
  const [text, setText] = useState("");
  useEffect(() => {
    api.letter(sender).then((l) => setText(l.text));
  }, [sender]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast("הועתק. אפשר להדביק במייל");
    } catch {
      toast("לא הצלחתי להעתיק. סמני והעתיקי ידנית");
    }
  }
  return (
    <section className="card letter">
      <h2>טיוטת מכתב ל{sender}</h2>
      <p className="muted">השולח שהטפסים שלו הצריכו הכי הרבה עבודה השבוע. המכתב לא נשלח מכאן.</p>
      <textarea className="input" value={text} onChange={(e) => setText(e.target.value)} rows={11} aria-label="נוסח המכתב" />
      <button className="btn primary" onClick={copy} disabled={!text}>
        לערוך ולשלוח
      </button>
    </section>
  );
}
