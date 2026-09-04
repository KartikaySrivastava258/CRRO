import { useCallback, useEffect, useRef, useState } from "react";

export function useApi(fn, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fnRef.current()
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, deps);

  useEffect(() => reload(), [reload]);

  return { data, loading, error, reload };
}
