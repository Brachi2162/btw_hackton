import { useEffect, useRef, useState } from "react";
import { useApp } from "../app-context";
import { VirusCanvas } from "../components/VirusCanvas";
import "./Home.css";

// The welcome page: what VERA does, told while the virus turns with the
// scroll. Every feature card links to the screen that does it.

const STEPS = [
  {
    k: "01",
    title: "הטפסים מגיעים",
    text: "סורק, מצלמה או פקס: הכול נוחת בתיקייה אחת. מדביקים את הנתיב, ו-VERA בוחרת לבד את מה שרלוונטי.",
  },
  {
    k: "02",
    title: "שלוש קריאות בלתי תלויות",
    text: "ברקוד וכללים, ושני מודלי ראייה שונים. כל אחד קורא את שבעת השדות בנפרד, בלי לראות מה קראו האחרים.",
  },
  {
    k: "03",
    title: "הסכמה ובדיקות",
    text: "ספרת ביקורת של ת.ז., תאריכים, רשימת שולחים. ערך מאושר רק כשמקורות מסכימים והבדיקות עוברות. כל השאר מסומן לעין אנושית.",
  },
  {
    k: "04",
    title: "אקסל מאומת",
    text: "רק אחרי שכל שדה מאושר או תוקן יוצא קובץ האקסל, וכל ערך בו עם ראיה: עמוד, מיקום והטקסט שממנו נקרא.",
  },
];

const FEATURES: { icon: string; title: string; text: string; to?: string; cta?: string }[] = [
  {
    icon: "⇣",
    title: "קליטה מתיקייה",
    text: "נתיב של תיקייה במחשב במקום גרירת קבצים. נקלטים רק מסמכים אמיתיים: בלי כפילויות, בלי קבצים זמניים ובלי מה שעדיין נסרק.",
    to: "intake",
    cta: "לקליטה",
  },
  {
    icon: "◎",
    title: "שלושה מקורות",
    text: "הביטחון נקבע לפי הסכמה בין מקורות בלתי תלויים ולפי ולידציה, לא לפי מה שהמודל אומר על עצמו.",
    to: "review",
    cta: "לבדיקה",
  },
  {
    icon: "▦",
    title: "חתימת טופס",
    text: "שבעה שדות, שבעה צבעים. במבט אחד רואים מה מאומת, מה סביר ומה מחכה לבדיקה.",
    to: "review",
    cta: "לראות",
  },
  {
    icon: "⌕",
    title: "עדשה וראיה",
    text: "כל ערך מוצג לצד החיתוך מהטופס עצמו, עם מה שכל מקור קרא. לא צריך לחפש בעמוד.",
    to: "review",
    cta: "לבדיקה",
  },
  {
    icon: "⟲",
    title: "תיקון אחד מתקן הכול",
    text: "תיקנת גורם שולח בטופס אחד? VERA מציעה להחיל את אותו תיקון על כל טופס באצווה שנקרא אותו דבר.",
  },
  {
    icon: "?",
    title: "למה?",
    text: "לכל שדה מסומן יש הסבר בעברית פשוטה: מי קרא מה, איזו בדיקה נכשלה, ולמה לא ניחשנו.",
  },
  {
    icon: "✉",
    title: "בירורים מול שולחים",
    text: "שדה חסר או ת.ז. לא תקינה פותחים בירור עם טיוטת מכתב מוכנה. מה שפתוח יותר מיממה עולה לראש.",
    to: "followups",
    cta: "לבירורים",
  },
  {
    icon: "▤",
    title: "דוח שבועי",
    text: "כמה שעות נחסכו, כמה אושר אוטומטית, ואיזה שולח מייצר הכי הרבה עבודה ידנית, עם מכתב מוכן אליו.",
    to: "reports",
    cta: "לדוח",
  },
  {
    icon: "✦",
    title: "העוזרת VERA",
    text: "שואלים בעברית: \"מה מחכה לי עכשיו?\". התשובה מגיעה מהנתונים, וכל שינוי מחכה לאישור שלך.",
  },
];

const RULES = [
  ["שדה ריק ומסומן", "עדיף על ערך מנוחש"],
  ["כל ערך", "מגיע עם ראיה"],
  ["הביטחון", "נקבע לפי הסכמה, לא לפי המודל"],
];

function CountUp({ value }: { value: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setN(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / 900);
      setN(Math.round(value * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <span className="num">{n}</span>;
}

export function Home() {
  const { overview, go } = useApp();
  const [stage, setStage] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  // reveal on scroll, and which story step is in the middle of the screen
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const reveal = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            reveal.unobserve(e.target);
          }
        }),
      { threshold: 0.12, rootMargin: "0px 0px -6% 0px" },
    );
    root.querySelectorAll(".rv").forEach((el) => reveal.observe(el));
    const steps = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) setStage(Number((e.target as HTMLElement).dataset.step));
        }),
      { rootMargin: "-45% 0px -45% 0px" },
    );
    root.querySelectorAll("[data-step]").forEach((el) => steps.observe(el));
    return () => {
      reveal.disconnect();
      steps.disconnect();
    };
  }, []);

  // the page scrolls (the story), but without a visible scrollbar
  useEffect(() => {
    document.documentElement.classList.add("no-scrollbar");
    return () => document.documentElement.classList.remove("no-scrollbar");
  }, []);

  // scroll progress
  useEffect(() => {
    const onScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      if (barRef.current) barRef.current.style.transform = `scaleX(${max > 0 ? window.scrollY / max : 0})`;
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // the spotlight follows the pointer inside a card
  const spot = (e: React.PointerEvent<HTMLElement>) => {
    const card = (e.target as HTMLElement).closest<HTMLElement>(".feat");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`);
    card.style.setProperty("--my", `${e.clientY - r.top}px`);
  };

  return (
    <div className="home" ref={rootRef}>
      <div className="home-progress" ref={barRef} />
      <div className="home-blobs" aria-hidden="true">
        <span className="b1" />
        <span className="b2" />
        <span className="b3" />
      </div>

      <section className="story">
        <div className="story-text">
          <header className="hero" data-step="0">
            <div className="eyebrow rv">VERA · המעבדה המרכזית לנגיפים</div>
            <h1 className="rv">
              מטופס סרוק
              <br />
              <span className="grad-text">לאקסל מאומת.</span>
              <br />
              בלי לנחש.
            </h1>
            <p className="lead rv">
              VERA קוראת טפסי התחייבות, מחלצת שבעה שדות ומצרפת ראיה לכל ערך. מה שלא ודאי, היא לא ממציאה: היא
              מסמנת אותו לעין שלך.
            </p>
            <div className="hero-cta rv">
              <button className="btn primary big" onClick={() => go("intake")}>
                לקליטת הבוקר ←
              </button>
              <button className="btn big glass" onClick={() => go("review")}>
                לבדיקת טפסים
              </button>
            </div>
            {overview && (
              <div className="hero-stats">
                <div>
                  <b>
                    <CountUp value={overview.review_waiting} />
                  </b>
                  <span>שדות מחכים לבדיקה</span>
                </div>
                <div>
                  <b>
                    <CountUp value={overview.open_followups} />
                  </b>
                  <span>בירורים פתוחים</span>
                </div>
                <div>
                  <b>
                    <CountUp value={overview.batches_not_exported} />
                  </b>
                  <span>אצוות לייצוא</span>
                </div>
              </div>
            )}
            <div className="scroll-hint" aria-hidden="true">
              <span />
              גללי כדי לראות איך זה עובד
            </div>
          </header>

          {STEPS.map((s, i) => (
            <article key={s.k} className={`step${stage === i + 1 ? " on" : ""}`} data-step={i + 1}>
              <div className="step-k grad-text num">{s.k}</div>
              <h2>{s.title}</h2>
              <p>{s.text}</p>
            </article>
          ))}
        </div>

        <div className="story-stage">
          <div className="stage-sticky">
            <VirusCanvas stage={stage} />
            <div className="stage-caption" key={stage}>
              {stage === 0 ? "גררי את העכבר או גללי: הנגיף מסתובב" : STEPS[stage - 1].title}
            </div>
            <div className="stage-dots">
              {STEPS.map((s, i) => (
                <span key={s.k} className={stage >= i + 1 ? "on" : ""} />
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="rules">
        {RULES.map(([a, b], i) => (
          <div className="rule rv" key={a} style={{ transitionDelay: `${i * 90}ms` }}>
            <span className="rule-n num">{i + 1}</span>
            <div>
              <b>{a}</b>
              <span>{b}</span>
            </div>
          </div>
        ))}
      </section>

      <section className="features-wrap">
        <div className="section-head rv">
          <div className="eyebrow">מה יש כאן</div>
          <h2>
            כל מה שצריך לבוקר אחד, <span className="grad-text">במקום אחד</span>
          </h2>
        </div>
        <div className="features" onPointerMove={spot}>
          {FEATURES.map((f, i) => (
            <div className="feat rv" key={f.title} style={{ transitionDelay: `${(i % 3) * 80}ms` }}>
              <div className="feat-icon">{f.icon}</div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
              {f.to && (
                <button className="feat-go" onClick={() => go(f.to!)}>
                  {f.cta} ←
                </button>
              )}
            </div>
          ))}
        </div>
      </section>

      <footer className="home-foot rv">
        <div className="foot-card">
          <div>
            <h2>
              מוכנה להתחיל? <span className="grad-text">הבוקר כבר כאן.</span>
            </h2>
            <p className="muted">פותח בהאקתון AI קוד פתוח 2026, יחד עם המעבדה המרכזית לנגיפים, משרד הבריאות.</p>
            <button className="btn primary big" onClick={() => go("intake")}>
              לקליטת הבוקר ←
            </button>
          </div>
          <div className="foot-logo">
            <span>בשיתוף</span>
            <img src="/logo-partners.png" alt="משרד הבריאות · המעבדה המרכזית לנגיפים" width={229} height={92} />
          </div>
        </div>
      </footer>
    </div>
  );
}
