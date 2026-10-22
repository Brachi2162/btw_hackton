import { useEffect, useRef, useState } from "react";
import { api, type Batch, type InboxPreview, type WatchStatus } from "../api";
import { useApp } from "../app-context";
import { useToast } from "../components/Toast";
import { VirusBackground } from "../components/VirusBackground";
import { useUploadDone } from "../components/UploadDone";
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
  const { batchId, setBatchId, go, overview, refreshOverview } = useApp();
  const toast = useToast();
  const uploadDone = useUploadDone();
  const [over, setOver] = useState(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [rejected, setRejected] = useState<{ filename: string; error: string }[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  // Coming back to this screen mid-reading: the reading never stopped (it
  // runs on the server), so pick that batch up again instead of showing an
  // empty screen. The batch being read, else the one just uploaded to.
  useEffect(() => {
    let alive = true;
    api
      .batches()
      .then(async (list) => {
        const live = list.find((b) => b.status === "processing" && !b.demo);
        const mine = list.find((b) => b.id === batchId && b.status === "new");
        const id = live?.id ?? mine?.id;
        if (alive && id) setBatch(await api.batch(id));
      })
      .catch(() => {});
    return () => void (alive = false);
    // once, when the screen opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      setBatchId(b.id); // known before the upload ends: leaving mid-upload is fine
      const r = await api.upload(b.id, files);
      setRejected(r.rejected);
      if (r.added.length) {
        const pages = r.added.reduce((n, a) => n + a.pages, 0);
        uploadDone({
          title: "הקבצים עלו בהצלחה",
          detail: `${countHe(r.added.length, "קובץ", "קבצים")} · ${countHe(pages, "עמוד", "עמודים")} בקריאה`,
        });
      }
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

        <FolderImport
          onImported={(id, files) => {
            if (files) {
              uploadDone({ title: "הקבצים נקלטו מהתיקייה", detail: `${countHe(files, "קובץ", "קבצים")} בקריאה` });
            }
            follow(id);
          }}
        />

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
function FolderImport({ onImported }: { onImported: (id: string, files?: number) => void }) {
  const toast = useToast();
  const [path, setPath] = useState("");
  const [today, setToday] = useState(false); // re-imports are skipped anyway
  const [deep, setDeep] = useState(false);
  const [preview, setPreview] = useState<InboxPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [watch, setWatchState] = useState<WatchStatus | null>(null);

  // Watching a folder (D-017): the status refreshes while it is on.
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .watch()
        .then((w) => alive && setWatchState(w))
        .catch(() => {});
    load();
    const t = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  async function toggleWatch() {
    setBusy(true);
    setError(null);
    try {
      const on = !watch?.enabled;
      const w = await api.setWatch(on ? path : "", on, today, deep);
      setWatchState(w);
      toast(on ? "התיקייה במעקב: כל טופס חדש ייקלט לבד" : "המעקב הופסק");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  // remembered per computer: the scans folder rarely changes
  useEffect(() => {
    try {
      setPath(localStorage.getItem("vera-folder") ?? "");
    } catch {
      /* storage blocked */
    }
  }, []);

  async function check(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.inbox(path, today, deep));
      try {
        localStorage.setItem("vera-folder", path);
      } catch {
        /* storage blocked */
      }
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
      const r = await api.importFolder(path, today, deep);
      if (r.batch_id) {
        setPreview(null);
        onImported(r.batch_id, r.relevant);
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
      <p className="muted">
        הדביקי נתיב של תיקייה במחשב (או קישור file:///), או כתובת אינטרנט (http://…) של תיקיית הסורק ברשת. נקלטים
        רק טפסים שעוד לא נקלטו.
      </p>
      <form className="folder-row" onSubmit={check}>
        <input
          className="input num"
          dir="ltr"
          value={path}
          onChange={(e) => {
            const v = e.target.value;
            // A network scanner folder: by default take only today's scans.
            if (/^\s*https?:\/\//i.test(v) && !/^\s*https?:\/\//i.test(path)) setToday(true);
            setPath(v);
            setPreview(null);
          }}
          placeholder="C:\Scans\2026-10-22  או  http://scanner/scans/"
          aria-label="נתיב התיקייה או כתובת"
        />
        <label className="today">
          <input type="checkbox" checked={today} onChange={(e) => (setToday(e.target.checked), setPreview(null))} /> רק מהיום
        </label>
        <label className="today">
          <input type="checkbox" checked={deep} onChange={(e) => (setDeep(e.target.checked), setPreview(null))} /> כולל תתי-תיקיות
        </label>
        <button className="btn" disabled={busy}>
          בדיקה
        </button>
        <button
          type="button"
          className={`btn watch-btn${watch?.enabled ? " on" : ""}`}
          onClick={toggleWatch}
          disabled={busy || (!watch?.enabled && !path.trim())}
          aria-pressed={!!watch?.enabled}
        >
          {watch?.enabled ? "⏸ הפסקת מעקב" : "👁 מעקב אוטומטי"}
        </button>
      </form>
      {watch?.enabled && (
        <div className="watch-status" role="status">
          <span className="watch-dot" aria-hidden="true" />
          <span>
            התיקייה <span className="num" dir="ltr">{watch.folder}</span> במעקב: כל טופס חדש נקלט לבד (בדיקה כל{" "}
            {watch.interval} שניות{watch.today_only ? ", רק מהיום" : ""}).
          </span>
          <span className="muted">
            נקלטו היום: <span className="num">{watch.added_today}</span>
            {watch.last_check && ` · נבדק לאחרונה ${new Date(watch.last_check).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`}
          </span>
          {watch.batch_id && (
            <button className="link" onClick={() => onImported(watch.batch_id!)}>
              לאצווה של המעקב
            </button>
          )}
          {watch.error && <span className="fail-warn">{watch.error}</span>}
        </div>
      )}
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

/** "קובץ אחד", "2 קבצים": Hebrew counts read naturally. */
function countHe(n: number, one: string, many: string) {
  return n === 1 ? `${one} אחד` : `${n} ${many}`;
}
