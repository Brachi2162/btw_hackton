import { useState } from "react";
import { api, ApiError, type Propagation } from "../api";
import { useApp } from "../app-context";
import { useToast } from "../components/Toast";
import { FIELD_HE, showValue } from "../fields";
import "./fix.css";

/** Confirm or correct one field; reports the server's propagation offer. */
export function useFix(onSaved: (prop: Propagation | null) => void) {
  const toast = useToast();
  const { refreshOverview } = useApp();
  const [error, setError] = useState<{ message: string; retry: () => void } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(sid: string, field: string, value: string | null, force = false) {
    setBusy(true);
    setError(null);
    try {
      // value "": the form doesn't say; the field goes grey, for a follow-up
      const res =
        value === null
          ? await api.accept(sid, field)
          : await api.correct(sid, field, value === "" ? [] : [value], force);
      toast(
        value === null
          ? `${FIELD_HE[field]} אושר`
          : value === ""
            ? `${FIELD_HE[field]} סומן לבירור עם השולח`
            : `${FIELD_HE[field]} תוקן`,
      );
      refreshOverview();
      onSaved(res.propagation);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && value !== null) {
        setError({ message: String(e.detail), retry: () => run(sid, field, value, true) });
      } else {
        setError({ message: e instanceof Error ? e.message : String(e), retry: () => run(sid, field, value) });
      }
    } finally {
      setBusy(false);
    }
  }

  /** current: the value now in the field (choosing it = confirming it). */
  const choose = (sid: string, field: string, value: string, current: string[]) =>
    run(sid, field, current.length === 1 && current[0] === value ? null : value);

  return { run, choose, error, busy, clearError: () => setError(null) };
}

export function FixError({ error }: { error: { message: string; retry: () => void } | null }) {
  if (!error) return null;
  return (
    <div className="fix-error" role="alert">
      <span>{error.message}</span>
      <button className="link" onClick={error.retry}>
        לשמור בכל זאת
      </button>
    </div>
  );
}

/** "One fix that fixes everything": offer the confirmed value to the rest. */
export function PropagationBar({
  batchId,
  prop,
  onDone,
}: {
  batchId: string;
  prop: Propagation;
  onDone: (applied: number) => void;
}) {
  const toast = useToast();
  const { refreshOverview } = useApp();
  const [busy, setBusy] = useState(false);
  const what = FIELD_HE[prop.field] ?? prop.field;
  async function apply() {
    setBusy(true);
    try {
      const r = await api.propagate(batchId, prop);
      toast(`הוחל על ${r.applied.length} טפסים`);
      refreshOverview();
      onDone(r.applied.length);
    } catch (e) {
      toast(e instanceof Error ? e.message : "שגיאה");
      setBusy(false);
    }
  }
  return (
    <div className="prop-bar" role="region" aria-label="החלה על טפסים נוספים">
      {prop.kind === "fix" ? (
        <span>
          תיקנת {what} ל-
          <span className="num" dir="auto">
            {showValue(prop.field, prop.value)}
          </span>
          . ב-<span className="num">{prop.count}</span> טפסים נוספים באצווה יש אותה טעות (
          <span dir="auto">{(prop.from_values ?? []).map((v) => showValue(prop.field, v)).join(", ")}</span>). לתקן גם
          אותם?
        </span>
      ) : (
        <span>
          אישרת {what}{" "}
          <span className="num" dir="ltr">
            {showValue(prop.field, prop.value)}
          </span>
          . ל-<span className="num">{prop.count}</span> טפסים נוספים יש אותה חותמת. להחיל עליהם?
        </span>
      )}
      <div className="prop-actions">
        <button className="btn primary" onClick={apply} disabled={busy} autoFocus>
          {prop.kind === "fix" ? "לתקן" : "להחיל על"} <span className="num">{prop.count}</span> טפסים
        </button>
        <button className="btn dark" onClick={() => onDone(0)} disabled={busy}>
          רק כאן
        </button>
      </div>
    </div>
  );
}
