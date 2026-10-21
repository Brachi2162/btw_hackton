import { useEffect, useRef, useState } from "react";
import { api, type Batch } from "../api";
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

export function Intake() {
  const { setBatchId, go, refreshOverview } = useApp();
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

  async function start(files: File[]) {
    if (!files.length) return;
    const d = new Date();
    const name = `בוקר ${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
    try {
      const b = await api.createBatch(name);
      setBatchId(b.id);
      const r = await api.upload(b.id, files);
      setRejected(r.rejected);
      setBatch(await api.batch(b.id));
    } catch (e) {
      toast(e instanceof Error ? e.message : "ההעלאה נכשלה");
    }
  }

  const idx = stageIndex(batch);
  return (
    <div className="page intake">
      <VirusBackground />
      <div className="intake-inner">
        <h1>קליטה</h1>
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
                    <span style={{ width: `${d.pages ? ((d.done + d.failed) / d.pages) * 100 : 0}%` }} />
                  </span>
                  <span className="num muted">
                    {d.done}/{d.pages}
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
