import { useEffect, useRef, useState } from "react";
import { api, type ChatReply } from "../api";
import { useApp } from "../app-context";
import { RichText } from "../components/RichText";
import { Virus } from "../components/Virus";
import { useToast } from "../components/Toast";
import "./Assistant.css";

type Msg = { role: "user" | "assistant"; content: string; actions?: ChatReply["actions"] };
const START = ["מה מחכה לי עכשיו?", "אילו בירורים פתוחים יותר מיממה?", "איך היה השבוע?"];

/** "שאלי את VERA": a floating button and a 400x600 chat panel. Changes the
 * assistant proposes come back as confirm cards; nothing runs until the
 * user confirms. */
export function Assistant() {
  const { refreshOverview } = useApp();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [chips, setChips] = useState(START);
  const [handled, setHandled] = useState<Record<string, "ok" | "no">>({});
  const end = useRef<HTMLDivElement>(null);

  // Braces: newer Chrome returns a Promise from scrollIntoView, and React
  // would call an effect's return value as its cleanup ("n is not a function").
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [msgs, busy]);

  async function send(q: string) {
    q = q.trim();
    if (!q || busy) return;
    const next = [...msgs, { role: "user" as const, content: q }];
    setMsgs(next);
    setText("");
    setBusy(true);
    try {
      const r = await api.chat(next.map(({ role, content }) => ({ role, content })));
      setMsgs([...next, { role: "assistant", content: r.reply, actions: r.actions }]);
      if (r.suggestions?.length) setChips(r.suggestions);
    } catch (e) {
      setMsgs([...next, { role: "assistant", content: `לא הצלחתי לענות (${e instanceof Error ? e.message : "שגיאה"}).` }]);
    } finally {
      setBusy(false);
    }
  }

  async function confirm(key: string, a: NonNullable<Msg["actions"]>[number]) {
    try {
      await api.confirm(a.tool, a.args);
      setHandled((h) => ({ ...h, [key]: "ok" }));
      toast("בוצע");
      refreshOverview();
    } catch (e) {
      toast(e instanceof Error ? e.message : "לא בוצע");
    }
  }

  return (
    <>
      {!open && (
        <button className="ask-btn" onClick={() => setOpen(true)} aria-label="שאלי את VERA">
          <span className="ask-bubble">{msgs.length ? "חזרה לשיחה" : "שאלי את VERA"}</span>
          <span className="ask-orb">
            <Virus size={38} spikes={9} className="ask-virus" />
            {msgs.length > 0 && <span className="ask-dot" aria-hidden="true" />}
          </span>
        </button>
      )}
      {open && (
        <section className="chat" role="dialog" aria-label="שיחה עם VERA">
          <header>
            <Virus size={22} spikes={8} className="chat-logo" />
            <h2>VERA</h2>
            <span className="grow" />
            <button className="btn ghost dark-x" onClick={() => setOpen(false)} aria-label="הקטנה (השיחה נשמרת)" title="הקטנה">
              —
            </button>
          </header>
          <div className="chat-body">
            {!msgs.length && (
              <>
                <div className="chat-hello">
                  <span className="hello-avatar" aria-hidden="true">
                    <Virus size={40} spikes={9} className="hello-virus" />
                  </span>
                  <div className="hello-bubble">
                    <strong>היי, אני VERA</strong>
                    <span>אפשר לשאול אותי על הטפסים, המבחנות והבירורים של היום.</span>
                  </div>
                </div>
                <div className="starters">
                  {chips.map((c) => (
                    <button key={c} className="starter" onClick={() => send(c)} disabled={busy}>
                      <span className="starter-icon" aria-hidden="true">
                        {iconFor(c)}
                      </span>
                      <span>{c}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.role === "assistant" ? <RichText text={m.content} /> : <p>{m.content}</p>}
                {m.actions?.map((a, j) => {
                  const key = `${i}-${j}`;
                  return (
                    <div key={key} className="confirm-card">
                      <span>{a.summary_he}</span>
                      {handled[key] ? (
                        <span className="muted">{handled[key] === "ok" ? "✓ בוצע" : "בוטל"}</span>
                      ) : (
                        <div className="confirm-actions">
                          <button className="btn primary" onClick={() => confirm(key, a)}>
                            אישור
                          </button>
                          <button className="btn" onClick={() => setHandled((h) => ({ ...h, [key]: "no" }))}>
                            ביטול
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
            {busy && (
              <div className="msg assistant typing" role="status">
                <Virus size={26} spikes={9} className="typing-virus" />
                <span>VERA חושבת…</span>
              </div>
            )}
            <div ref={end} />
          </div>
          {msgs.length > 0 && (
            <div className="chips" aria-label="שאלות מוכנות">
              {chips.map((c) => (
                <button key={c} className="chip" onClick={() => send(c)} disabled={busy}>
                  <span aria-hidden="true">{iconFor(c)}</span> {c}
                </button>
              ))}
            </div>
          )}
          <form
            className="chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
            }}
          >
            <textarea
              className="input chat-text"
              rows={2}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                // grow with the question, up to about five lines
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 130)}px`;
              }}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter starts a new line
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(text);
                }
              }}
              placeholder="כתבי שאלה… (Enter לשליחה, Shift+Enter לשורה חדשה)"
              aria-label="שאלה ל-VERA"
            />
            <button className="btn primary" disabled={!text.trim() || busy}>
              שליחה
            </button>
          </form>
        </section>
      )}
    </>
  );
}

/** A small icon per ready question, by its topic. */
function iconFor(q: string): string {
  if (/מחכה|עכשיו/.test(q)) return "📋";
  if (/בירור|פני/.test(q)) return "🔔";
  if (/שבוע|דוח/.test(q)) return "📈";
  if (/מבחנ/.test(q)) return "🧪";
  if (/שולח|בית חולים/.test(q)) return "🏥";
  return "💬";
}
