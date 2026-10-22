import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, type Overview } from "./api";
import { AppCtx } from "./app-context";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { UploadDoneProvider } from "./components/UploadDone";
import { useRoute } from "./hooks";
import { Assistant } from "./screens/Assistant";
import { Review } from "./screens/Review";
import "./App.css";

// Review is the first screen people use; the rest load on demand.
const Home = lazy(() => import("./screens/Home").then((m) => ({ default: m.Home })));
const Intake = lazy(() => import("./screens/Intake").then((m) => ({ default: m.Intake })));
const Followups = lazy(() => import("./screens/Followups").then((m) => ({ default: m.Followups })));
const Weekly = lazy(() => import("./screens/Weekly").then((m) => ({ default: m.Weekly })));
const Export = lazy(() => import("./screens/Export").then((m) => ({ default: m.Export })));
const Handover = lazy(() => import("./screens/Handover").then((m) => ({ default: m.Handover })));

const SCREENS: Record<string, React.ComponentType> = {
  home: Home,
  intake: Intake,
  review: Review,
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
  const screen = SCREENS[route[0]] ? route[0] : "home";

  // The sidebar counts the open batch, so the overview is asked for it; a
  // ref keeps refreshOverview stable for the screens that call it.
  const batchRef = useRef(batchId);
  batchRef.current = batchId;
  const refreshOverview = useCallback(() => {
    api
      .overview(batchRef.current)
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

  // Another batch opened (picker, new upload): its count, now.
  useEffect(() => {
    if (batchId) refreshOverview();
  }, [batchId, refreshOverview]);

  const Screen = SCREENS[screen];
  return (
    <AppCtx.Provider value={{ batchId, setBatchId: setBatch, overview, refreshOverview, go }}>
      <ToastProvider>
      <UploadDoneProvider>
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
      </UploadDoneProvider>
      </ToastProvider>
    </AppCtx.Provider>
  );
}
