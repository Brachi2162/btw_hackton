import { useState } from "react";
import { api, type Followup } from "../api";
import { useApp } from "../app-context";
import { DemoBadge } from "../components/BatchBar";
import { useToast } from "../components/Toast";
import { useData, useTick } from "../hooks";
import "./Followups.css";

const WEEK = 7 * 24 * 3600 * 1000;
const COLUMNS: { key: Followup["status"]; title: string }[] = [
  { key: "open", title: "פתוח" },
  { key: "sent", title: "נשלחה פנייה" },
  { key: "closed", title: "נסגר השבוע" },
];
const KIND_HE: Record<Followup["kind"], string> = {
  missing_field: "שדה חסר",
  invalid_id: "ת.ז. לא תקינה",
  tube_orphan: "מבחנה בלי טופס",
};

function age(created: string, now: number): string {
  const ms = now - new Date(created).getTime();
  const h = Math.floor(ms / 3600000);
  if (h >= 48) return `${Math.floor(h / 24)} ימים`;
  if (h >= 1) return `${h} שע׳ ${Math.floor((ms % 3600000) / 60000)} דק׳`;
  return `${Math.max(0, Math.floor(ms / 60000))} דק׳`;
}

export function Followups() {
  const { refreshOverview } = useApp();
  const { data, refresh } = useData(() => api.followups(), [], 30000);
  const now = useTick(30000);
  const items = data ?? [];
  const cols = COLUMNS.map((c) => ({
    ...c,
    items: items.filter(
      (f) =>
        f.status === c.key &&
        (c.key !== "closed" || (f.closed_at && now - new Date(f.closed_at).getTime() < WEEK)),
    ),
  }));
  const changed = () => {
    refresh();
    refreshOverview();
  };

  return (
    <div className="page followups">
      <div className="page-head">
        <h1>בירורים</h1>
        <DemoBadge show={items.some((f) => f.demo)} />
        <span className="muted">
          <span className="num">{items.filter((f) => f.overdue).length}</span> פתוחים יותר מיממה
        </span>
      </div>
      <div className="board">
        {cols.map((c) => (
          <section key={c.key} className={`column col-${c.key}`} aria-label={c.title}>
            <h2>
              {c.title} <span className="num muted">{c.items.length}</span>
            </h2>
            <div className="column-body">
              {c.items.map((f) => (
                <Card key={f.id} f={f} now={now} onChange={changed} />
              ))}
              {!c.items.length && <p className="muted empty-col">אין</p>}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function Card({ f, now, onChange }: { f: Followup; now: number; onChange: () => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState<string | null>(f.draft);
  const [busy, setBusy] = useState(false);
  const overdue = f.status === "open" && now - new Date(f.created_at).getTime() > 24 * 3600 * 1000;

  async function ask() {
    setBusy(true);
    try {
      const r = await api.draft(f.id);
      setDraft(r.draft);
      toast(r.source === "model" ? "VERA ניסחה טיוטה" : "נוסחה טיוטה מתבנית");
    } finally {
      setBusy(false);
    }
  }
  async function move(status: Followup["status"]) {
    await api.patchFollowup(f.id, { status, ...(draft !== f.draft && draft ? { draft } : {}) });
    toast(status === "sent" ? "סומן: נשלחה פנייה" : status === "closed" ? "הבירור נסגר" : "הבירור נפתח מחדש");
    onChange();
  }
  async function copy() {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft);
      toast("הטיוטה הועתקה");
    } catch {
      toast("לא הצלחתי להעתיק");
    }
  }

  return (
    <article className={`fu card${overdue ? " overdue" : ""}`}>
      <div className="fu-top">
        <span className={`badge kind-${f.kind}`}>{KIND_HE[f.kind]}</span>
        <span className={`timer num${overdue ? " late" : ""}`} title={new Date(f.created_at).toLocaleString("he-IL")}>
          ⏱ {age(f.created_at, now)}
        </span>
      </div>
      <h3>{f.title_he}</h3>
      {(f.sender || f.detail_he) && (
        <p className="muted fu-detail">{[f.sender, f.detail_he].filter(Boolean).join(" · ")}</p>
      )}
      {draft !== null && (
        <textarea className="input fu-draft" value={draft} onChange={(e) => setDraft(e.target.value)} rows={7} aria-label="טיוטת הפנייה" />
      )}
      <div className="fu-actions">
        {f.status !== "closed" && (
          <button className="btn" onClick={draft ? copy : ask} disabled={busy}>
            {busy ? "מנסחת…" : draft ? "העתקת הטיוטה" : "לנסח פנייה"}
          </button>
        )}
        {f.status === "open" && (
          <button className="btn" onClick={() => move("sent")}>
            נשלחה
          </button>
        )}
        {f.status !== "closed" ? (
          <button className="btn ghost" onClick={() => move("closed")}>
            סגירה
          </button>
        ) : (
          <button className="btn ghost" onClick={() => move("open")}>
            פתיחה מחדש
          </button>
        )}
      </div>
    </article>
  );
}
