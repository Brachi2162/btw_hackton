import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch on mount and when deps change; optional polling. */
export function useData<T>(load: () => Promise<T>, deps: unknown[], pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    setData(null);
    loadRef
      .current()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e?.message ?? e)));
    if (!pollMs) return () => void (alive = false);
    const t = setInterval(() => alive && refresh(), pollMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, refresh, setData };
}

export function useReducedMotion(): boolean {
  const q = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof matchMedia !== "undefined" && matchMedia(q).matches);
  useEffect(() => {
    if (typeof matchMedia === "undefined") return;
    const m = matchMedia(q);
    const on = () => setReduced(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Element width, kept current with a ResizeObserver. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Re-render every `ms` (for live timers). */
export function useTick(ms: number): number {
  const [t, setT] = useState(() => Date.now());
  useEffect(() => {
    const i = setInterval(() => setT(Date.now()), ms);
    return () => clearInterval(i);
  }, [ms]);
  return t;
}

/** Hash routing: #/review/abc -> ["review", "abc"]. */
export function useRoute(): [string[], (path: string) => void] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = useState(parse);
  useEffect(() => {
    const on = () => setParts(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [parts, (p: string) => (window.location.hash = `#/${p}`)];
}
