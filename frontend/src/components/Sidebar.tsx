import { useApp } from "../app-context";
import { Virus } from "./Virus";
import "./Sidebar.css";

const ITEMS: { key: string; label: string; icon: string }[] = [
  { key: "home", label: "ברוכים הבאים", icon: "✦" },
  { key: "intake", label: "קליטה", icon: "⇣" },
  { key: "review", label: "בדיקה", icon: "◉" },
  { key: "followups", label: "בירורים", icon: "↺" },
  { key: "reports", label: "דוחות", icon: "▥" },
  { key: "export", label: "ייצוא", icon: "⇪" },
];

export function Sidebar({ active }: { active: string }) {
  const { overview, go } = useApp();
  // the open batch, counted like its review screen (yellow + orange)
  const waiting = overview?.review_waiting_batch ?? overview?.review_waiting ?? 0;
  return (
    <nav className="sidebar" aria-label="ניווט ראשי">
      <button className="logo" onClick={() => go("home")} aria-label="VERA, עמוד הבית">
        <Virus size={30} spikes={9} className="logo-icon" />
        <span className="logo-text">VERA</span>
      </button>
      <ul>
        {ITEMS.map((it) => (
          <li key={it.key}>
            <button
              className={`nav-item${active === it.key ? " active" : ""}`}
              aria-current={active === it.key ? "page" : undefined}
              onClick={() => go(it.key)}
            >
              <span className="nav-icon" aria-hidden="true">
                {it.icon}
              </span>
              <span className="nav-label">{it.label}</span>
              {it.key === "review" && waiting > 0 && (
                <span className="nav-count num" aria-label={`${waiting} שדות מחכים`}>
                  {waiting}
                </span>
              )}
              {it.key === "followups" && (overview?.overdue_followups ?? 0) > 0 && (
                <span className="nav-dot" aria-label="יש בירורים באיחור" />
              )}
            </button>
          </li>
        ))}
      </ul>
      <div className="sidebar-foot">
        <button className={`nav-item small${active === "handover" ? " active" : ""}`} onClick={() => go("handover")}>
          <span className="nav-icon" aria-hidden="true">
            ⇄
          </span>
          <span className="nav-label">העברת משמרת</span>
        </button>
        <div className="listening">
          <span className="live-dot" aria-hidden="true" />
          מאזינה לתיקיית הסורק
        </div>
        <div className="partner-logo">
          <img src="/logo-partners.png" alt="משרד הבריאות · המעבדה המרכזית לנגיפים" width={229} height={92} />
        </div>
      </div>
    </nav>
  );
}
