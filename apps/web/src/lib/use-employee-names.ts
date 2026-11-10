"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Employee } from "@/lib/types";

const cache = new Map<string, string>();

/**
 * Resolves employee ids to "Full Name (CODE)". GET /attendance/events returns only
 * `employee_id` (API_CONTRACTS §2.8), so names are looked up once per id and cached for the
 * session. Backend improvement noted in DEVELOPMENT_PROGRESS: embed an employee summary in
 * event responses.
 */
export function useEmployeeNames(ids: (string | null)[]): Record<string, string> {
  const [, force] = useState(0);
  const key = [...new Set(ids.filter((x): x is string => Boolean(x)))].sort().join(",");

  useEffect(() => {
    const missing = key ? key.split(",").filter((id) => !cache.has(id)) : [];
    if (missing.length === 0) return;
    let alive = true;
    Promise.all(
      missing.slice(0, 50).map((id) =>
        api
          .get<Employee>(`/employees/${id}`)
          .then((e) => cache.set(id, `${e.full_name} (${e.employee_code})`))
          .catch(() => cache.set(id, `${id.slice(0, 8)}…`)),
      ),
    ).then(() => alive && force((n) => n + 1));
    return () => {
      alive = false;
    };
  }, [key]);

  return Object.fromEntries(key ? key.split(",").map((id) => [id, cache.get(id) ?? "…"]) : []);
}
