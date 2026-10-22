import { useEffect, useRef, useState } from "react";
import { api, type Batch, type InboxPreview } from "../api";
import { useApp } from "../app-context";
import { useToast } from "../components/Toast";
import { VirusBackground } from "../components/VirusBackground";
import "./Intake.css";

const STAGES = ["פיצול", "זיהוי שולח", "קריאה משלושה מקורות", "אימות"];
// page stage from the server -> how many stages are done
const DONE_UPTO: Record<string, number> = { split: 0, read: 2, verify: 3 };

function stageIndex(b: Batch | null): number {
  if (!b) return -1;
  if (b.status === "done" || b.status === "exported") return STAGES.length;
  const st = b.documents.flatMap((d) => d.stages).filter(Boolean) as string[];
  if (!st.length) return b.status === "processing" ? 0 : -1;
  return Math.min(...st.map((s) => DONE_UPTO[s] ?? 0));
}

const todayName = () => {
  const d = new Date();
  return `בוקר ${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
};

export function Intake() {
  const { setBatchId, go, overview, refreshOverview } = useApp();
  const toast = useToast();
  const [over, setOver] = useState(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [rejected, setRejected] = useState<{ filename: string; error: string }[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!batch || batch.status === "done") return;
    const t = setInterval(() => api.batch(batch.id).then(setBatch), 1000);
    return () => clearInterval(t);
  }, [batch]);

  useEffect(() => {
    if (batch?.status === "done") refreshOverview();
  }, [batch?.status, refreshOverview]);

  async function follow(id: string) {
    setBatchId(id);
    setBatch(await api.batch(id));
  }

  async function start(files: File[]) {
    if (!files.length) return;
    try {
      const b = await api.createBatch(todayName());
      const r = await api.upload(b.id, files);
      setRejected(r.rejected);
      await follow(b.id);
    } catch (e) {
      toast(e instanceof Error ? e.message : "ההעלאה נכשלה");
    }
  }

  const idx = stageIndex(batch);
  const failed = batch?.documents.reduce((n, d) => n + d.failed, 0) ?? 0;
  const errors = [...new Set(batch?.documents.flatMap((d) => d.errors) ?? [])];

  return (
    <div className="page intake">
      <VirusBackground />
      <div className="intake-inner">
        <h1>קליטה</h1>

        {overview?.vlm_problem && (
          <p className="setup-warn" role="alert">
            ⚠ {overview.vlm_problem}
          </p>
        )}

        <FolderImport onImported={follow} defaultPath={overview?.scan_folder ?? ""} />

        <div
          className={`drop${over ? " over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => fileInput.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileInput.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            start(Array.from(e.dataTransfer.files));
          }}
        >
          <span className="drop-title">גררי לכאן את טפסי הבוקר</span>
          <span className="muted">PDF, JPG, PNG או HEIC · או לחצי לבחירה</span>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept=".pdf,.jpg,.jpeg,.png,.heic,application/pdf,image/*"
            onChange={(e) => start(Array.from(e.target.files ?? []))}
          />
        </div>

        {batch && (
          <section className="card progress-card" aria-live="polite">
            <ol className="stages">
              {STAGES.map((s, i) => (
                <li key={s} className={i < idx ? "done" : i === idx ? "now" : ""}>
                  <span className="dot" aria-hidden="true">
                    {i < idx ? "✓" : i + 1}
                  </span>
                  {s}
                </li>
              ))}
            </ol>
            <ul className="files">
              {batch.documents.map((d) => (
                <li key={d.id}>
                  <span className="fname">{d.filename}</span>
                  <span className="fbar">
                    <span style={{ width: `${d.pages ? (d.done / d.pages) * 100 : 0}%` }} />
                    <span className="fbar-failed" style={{ width: `${d.pages ? (d.failed / d.pages) * 100 : 0}%` }} />
                  </span>
                  <span className="num muted">
                    {d.done}/{d.pages}
                    {d.failed > 0 && <span className="failed-n"> · {d.failed} נכשלו</span>}
                  </span>
                </li>
              ))}
              {rejected.map((r) => (
                <li key={r.filename} className="rejected">
                  <span className="fname">{r.filename}</span>
                  <span>{r.error}</span>
                </li>
              ))}
            </ul>
            {failed > 0 &&
              errors.map((e) => (
                <p key={e} className="fail-warn" role="alert">
                  {failed} עמודים לא נקראו: {e}
                </p>
              ))}
            {batch.quality_warnings.map((w) => (
              <p key={w.page_id} className="blur-warn" role="alert">
                ⚠ {w.message_he}
              </p>
            ))}
            {batch.status === "done" && (
              <button className="btn primary" onClick={() => go("review")}>
                לבדיקה
              </button>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

/** Import from the scanner folder: the server takes only the relevant files
 * (documents, not yet imported), no upload through the browser. */
function FolderImport({ onImported, defaultPath }: { onImported: (id: string) => void; defaultPath: string }) {
  const toast = useToast();
  const [path, setPath] = useState("");
  const [today, setToday] = useState(false); // re-imports are skipped anyway
  const [preview, setPreview] = useState<InboxPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!path && defaultPath) setPath(defaultPath);
  }, [defaultPath, path]);

  async function check(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.inbox(path, today));
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true);
    try {
      const r = await api.importFolder(path, today);
      if (r.batch_id) {
        toast(`נקלטו ${r.relevant} קבצים מהתיקייה`);
        setPreview(null);
        onImported(r.batch_id);
      } else {
        toast("אין קבצים חדשים בתיקייה");
        setPreview(r);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const skippedBy = preview
    ? Object.entries(
        preview.files
          .filter((f) => !f.relevant)
          .reduce<Record<string, number>>((m, f) => ({ ...m, [f.reason_he]: (m[f.reason_he] ?? 0) + 1 }), {}),
      )
    : [];

  return (
    <section className="card folder-card">
      <h2>קליטה מתיקייה</h2>
      <p className="muted">הדביקי את הנתיב לתיקיית הסריקות. נקלטים רק טפסים שעוד לא נקלטו.</p>
      <form className="folder-row" onSubmit={check}>
        <input
          className="input num"
          dir="ltr"
          value={path}
          onChange={(e) => {
            setPath(e.target.value);
            setPreview(null);
          }}
          placeholder="C:\Scans\2026-10-22"
          aria-label="נתיב התיקייה"
        />
        <label className="today">
          <input type="checkbox" checked={today} onChange={(e) => (setToday(e.target.checked), setPreview(null))} /> רק מהיום
        </label>
        <button className="btn" disabled={busy}>
          בדיקה
        </button>
      </form>
      {error && (
        <p className="fail-warn" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div className="folder-preview">
          <p>
            <strong className="num">{preview.relevant}</strong> קבצים רלוונטיים
            {skippedBy.length > 0 && (
              <span className="muted">
                {" "}
                · דילוג על {skippedBy.map(([why, n]) => `${n} (${why})`).join(", ")}
              </span>
            )}
          </p>
          {preview.relevant > 0 && (
            <ul className="folder-files">
              {preview.files
                .filter((f) => f.relevant)
                .slice(0, 8)
                .map((f) => (
                  <li key={f.path} className="num" dir="ltr">
                    {f.path}
                  </li>
                ))}
              {preview.relevant > 8 && <li className="muted">ועוד {preview.relevant - 8}</li>}
            </ul>
          )}
          <button className="btn primary" onClick={run} disabled={busy || !preview.relevant}>
            קליטת <span className="num">{preview.relevant}</span> קבצים
          </button>
        </div>
      )}
    </section>
  );
}
