import { useCallback, useEffect, useState } from "react";

export function usePollingResource(loader, dependencies = [], intervalMs = 60000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const payload = await loader();
      setData(payload);
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  }, dependencies);

  useEffect(() => {
    let cancelled = false;

    async function runInitialLoad() {
      setLoading(true);
      setError("");
      try {
        const payload = await loader();
        if (!cancelled) {
          setData(payload);
        }
      } catch (err) {
        if (!cancelled) {
          setError(String(err?.message || err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    runInitialLoad();
    const timer = window.setInterval(runInitialLoad, intervalMs);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loader, intervalMs]);

  return { data, loading, error, refresh };
}
