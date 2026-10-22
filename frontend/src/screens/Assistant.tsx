import { useEffect, useRef, useState } from "react";
import { api, type ChatReply } from "../api";
import { useApp } from "../app-context";
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
            {!msgs.length && <p className="chat-hello">היי, אני VERA. אפשר לשאול אותי על הטפסים, המבחנות והבירורים של היום.</p>}
            {msgs.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                <p>{m.content}</p>
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
            {busy && <div className="msg assistant typing">…</div>}
            <div ref={end} />
          </div>
          <div className="chips">
            {chips.map((c) => (
              <button key={c} className="chip" onClick={() => send(c)} disabled={busy}>
                {c}
              </button>
            ))}
          </div>
          <form
            className="chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
            }}
          >
            <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="כתבי שאלה…" aria-label="שאלה ל-VERA" />
            <button className="btn primary" disabled={!text.trim() || busy}>
              שליחה
            </button>
          </form>
        </section>
      )}
    </>
  );
}
