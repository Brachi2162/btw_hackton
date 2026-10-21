import { createContext, useCallback, useContext, useRef, useState } from "react";
import "./Toast.css";

const Ctx = createContext<(msg: string) => void>(() => {});

export const useToast = () => useContext(Ctx);

/** A small dark message at the top centre; gone after 2.5 seconds. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<{ msg: string; id: number } | null>(null);
  const timer = useRef<number>();
  const show = useCallback((msg: string) => {
    setToast((t) => ({ msg, id: (t?.id ?? 0) + 1 }));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 2500);
  }, []);
  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="toast-slot" role="status" aria-live="polite">
        {toast && (
          <div className="toast" key={toast.id}>
            {toast.msg}
          </div>
        )}
      </div>
    </Ctx.Provider>
  );
}
