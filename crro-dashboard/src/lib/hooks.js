import { useCallback, useEffect, useRef, useState } from "react";

// Loads a resource; keeps last good data on error (marked stale), aborts on
// unmount/reload, optional polling. No refetch loops: deps are explicit.
export function useResource(loader, deps = [], { pollMs } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: true, loadedAt: null });
  const loaderRef = useRef(loader); loaderRef.current = loader;
  const ctrlRef = useRef(null);
  const load = useCallback(async () => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController(); ctrlRef.current = ctrl;
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await loaderRef.current(ctrl.signal);
      if (!ctrl.signal.aborted) setState({ data, error: null, loading: false, loadedAt: new Date() });
    } catch (error) {
      if (!ctrl.signal.aborted) setState((s) => ({ ...s, error, loading: false }));
    }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); return () => ctrlRef.current?.abort(); }, [load]);
  useEffect(() => {
    if (!pollMs) return undefined;
    const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, pollMs);
    return () => clearInterval(t);
  }, [load, pollMs]);
  return { ...state, reload: load, stale: Boolean(state.error && state.data) };
}

export function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  return online;
}

// Hash router: "#/cases/abc?status=X" -> { path: "/cases/abc", parts: [...], query }
export function useRoute() {
  const parse = () => {
    const h = window.location.hash.replace(/^#/, "") || "/";
    const [path, qs] = h.split("?");
    return { path, parts: path.split("/").filter(Boolean), query: Object.fromEntries(new URLSearchParams(qs || "")) };
  };
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const f = () => { setRoute(parse()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f);
  }, []);
  return route;
}

export const go = (path, query) => {
  const qs = query ? "?" + new URLSearchParams(Object.entries(query).filter(([, v]) => v)) : "";
  window.location.hash = path + (qs === "?" ? "" : qs);
};
