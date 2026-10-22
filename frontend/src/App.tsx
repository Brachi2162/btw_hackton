import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, type Overview } from "./api";
import { AppCtx } from "./app-context";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { useRoute } from "./hooks";
import { Assistant } from "./screens/Assistant";
import { Review } from "./screens/Review";
import "./App.css";

// Review is the first screen people use; the rest load on demand.
const Intake = lazy(() => import("./screens/Intake").then((m) => ({ default: m.Intake })));
const CardMode = lazy(() => import("./screens/CardMode").then((m) => ({ default: m.CardMode })));
const Tubes = lazy(() => import("./screens/Tubes").then((m) => ({ default: m.Tubes })));
const Followups = lazy(() => import("./screens/Followups").then((m) => ({ default: m.Followups })));
const Weekly = lazy(() => import("./screens/Weekly").then((m) => ({ default: m.Weekly })));
const Export = lazy(() => import("./screens/Export").then((m) => ({ default: m.Export })));
const Handover = lazy(() => import("./screens/Handover").then((m) => ({ default: m.Handover })));

const SCREENS: Record<string, React.ComponentType> = {
  intake: Intake,
  review: Review,
  cards: CardMode,
  tubes: Tubes,
  followups: Followups,
  reports: Weekly,
  export: Export,
  handover: Handover,
};

export function App() {
  const [route, go] = useRoute();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [batchId, setBatch] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const screen = SCREENS[route[0]] ? route[0] : "review";

  const refreshOverview = useCallback(() => {
    api
      .overview()
      .then((o) => {
        setOverview(o);
        setOffline(false);
        setBatch((cur) => cur ?? o.latest_batch_id);
      })
      .catch(() => setOffline(true));
  }, []);

  useEffect(() => {
    refreshOverview();
    const t = setInterval(refreshOverview, 15000);
    return () => clearInterval(t);
  }, [refreshOverview]);

  const Screen = SCREENS[screen];
  return (
    <AppCtx.Provider value={{ batchId, setBatchId: setBatch, overview, refreshOverview, go }}>
      <ToastProvider>
        <div className="shell">
          <Sidebar active={screen} />
          <main className={`main main-${screen}`}>
            {offline && (
              <div className="offline-bar" role="alert">
                אין חיבור לשרת של VERA. העבודה שנשמרה לא אבדה; המסך יתחבר מחדש לבד.{" "}
                <button className="link" onClick={refreshOverview}>
                  לנסות עכשיו
                </button>
              </div>
            )}
            <ErrorBoundary key={screen} label="המסך הזה">
              <Suspense fallback={<div className="page empty">טוען…</div>}>
                <Screen />
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
        <ErrorBoundary label="הצ'אט עם VERA">
          <Assistant />
        </ErrorBoundary>
      </ToastProvider>
    </AppCtx.Provider>
  );
}
