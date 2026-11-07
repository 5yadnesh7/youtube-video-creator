"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";

/**
 * Fetches once on mount, then keeps re-fetching every `intervalMs` while
 * `shouldPoll(data)` is true. Resumes automatically if shouldPoll flips back
 * to true (e.g. after the user triggers a new render).
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  shouldPoll: (data: T) => boolean,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  const pollRef = useRef(shouldPoll);
  const dataRef = useRef<T | null>(null);

  useEffect(() => {
    fetcherRef.current = fetcher;
    pollRef.current = shouldPoll;
  });

  const refresh = useCallback(async () => {
    try {
      const next = await fetcherRef.current();
      dataRef.current = next;
      setData(next);
      setError(null);
      return next;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const set = useCallback((next: T) => {
    dataRef.current = next;
    setData(next);
    setError(null);
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = async () => {
      const d = dataRef.current;
      if (d === null || pollRef.current(d)) await refresh();
      if (!stopped) timer = setTimeout(loop, intervalMs);
    };
    void refresh().then(() => {
      if (!stopped) timer = setTimeout(loop, intervalMs);
    });
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  return { data, error, loading, refresh, set };
}
