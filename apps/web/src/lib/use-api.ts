"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type QueryState<T> = {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
};

type Result<T> = { key: string | null; data: T | undefined; error: unknown };

/**
 * GET `path` with loading / error / reload state. `path = null` skips the request.
 * `loading` is derived (the latest request key has no result yet), so no state is set
 * synchronously inside the effect, and stale responses from older keys are ignored.
 * Previous data stays visible while a reload is in flight.
 */
export function useApiQuery<T>(path: string | null): QueryState<T> {
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${path}#${tick}`;
  const [result, setResult] = useState<Result<T>>({ key: null, data: undefined, error: null });

  useEffect(() => {
    if (key === null || path === null) return;
    let active = true;
    api
      .get<T>(path)
      .then((data) => {
        if (active) setResult({ key, data, error: null });
      })
      .catch((error: unknown) => {
        if (active) setResult((prev) => ({ key, data: prev.data, error }));
      });
    return () => {
      active = false;
    };
  }, [key, path]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  const current = result.key === key;
  return {
    data: result.data,
    error: current ? result.error : null,
    loading: key !== null && !current,
    reload,
  };
}
