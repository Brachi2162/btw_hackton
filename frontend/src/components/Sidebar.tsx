import { useApp } from "../app-context";
import { Virus } from "./Virus";
import "./Sidebar.css";

const ITEMS: { key: string; label: string; icon: string }[] = [
  { key: "intake", label: "קליטה", icon: "⇣" },
  { key: "review", label: "בדיקה", icon: "◉" },
  { key: "cards", label: "כרטיסיות", icon: "▤" },
  { key: "tubes", label: "מבחנות", icon: "⫼" },
  { key: "followups", label: "בירורים", icon: "↺" },
  { key: "reports", label: "דוחות", icon: "▥" },
  { key: "export", label: "ייצוא", icon: "⇪" },
];

export function Sidebar({ active }: { active: string }) {
  const { overview, go } = useApp();
  const waiting = overview?.review_waiting ?? 0;
  return (
    <nav className="sidebar" aria-label="ניווט ראשי">
      <div className="logo">
        <Virus size={30} spikes={9} className="logo-icon" />
        <span className="logo-text">VERA</span>
      </div>
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
      </div>
    </nav>
  );
}
