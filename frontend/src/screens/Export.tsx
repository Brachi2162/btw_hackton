import { useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../app-context";
import { BatchPicker, Counters, DemoBadge } from "../components/BatchBar";
import { useToast } from "../components/Toast";
import { useData } from "../hooks";

/** xlsx export. Blocked while orange fields are unapproved (spec 6.3). */
export function Export() {
  const { batchId, go, refreshOverview } = useApp();
  const toast = useToast();
  const b = useData(() => (batchId ? api.batch(batchId) : Promise.resolve(null)), [batchId]);
  const [blocked, setBlocked] = useState<{ message: string; n: number } | null>(null);

  async function run(force = false) {
    if (!batchId) return;
    try {
      const blob = await api.exportBatch(batchId, force);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `vera-${b.data?.name ?? batchId}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
      toast("הקובץ ירד והאצווה ננעלה");
      setBlocked(null);
      b.refresh();
      refreshOverview();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message: string; fields: unknown[] };
        setBlocked({ message: d.message, n: d.fields.length });
      } else toast(e instanceof Error ? e.message : "הייצוא נכשל");
    }
  }

  if (!batchId) return <div className="page empty">אין עדיין אצוות.</div>;
  const data = b.data;
  return (
    <div className="page">
      <div className="page-head">
        <h1>ייצוא</h1>
        {data && <span className="muted">{data.name}</span>}
        <DemoBadge show={data?.demo} />
        <span className="grow" />
        <BatchPicker />
      </div>
      <Counters counts={data?.counts} />
      <section className="card" style={{ padding: 22, display: "flex", flexDirection: "column", gap: 12, alignItems: "flex-start", maxWidth: 720 }}>
        {data?.locked ? (
          <p>האצווה יוצאה וננעלה. אפשר להוריד שוב את אותו קובץ.</p>
        ) : (
          <p>קובץ Excel מאומת: גיליון התחייבויות, גיליון ביקורת עם כל תיקון, וסיכום לפי שולח.</p>
        )}
        <button className="btn primary" onClick={() => run(false)}>
          ייצוא ל-Excel
        </button>
        {blocked && (
          <div className="fix-error" role="alert" style={{ flexDirection: "column", alignItems: "flex-start" }}>
            <strong>{blocked.message}</strong>
            <span>אפשר לעבור עליהם בבדיקה, או לייצא בכל זאת (התאים יישארו צבועים והייצוא יירשם ביומן).</span>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn" onClick={() => go("review")}>
                לבדיקה
              </button>
              <button className="btn" onClick={() => run(true)}>
                לייצא בכל זאת
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
