"use client";

/**
 * Authentication state for the whole app. The server enforces every permission. The UI only
 * uses permissions to hide actions the user cannot perform anyway.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, type CurrentUser, type MeResponse } from "@/lib/api";

type Status = "loading" | "authenticated" | "anonymous";

export type BranchInfo = { id: string; name: string };

type AuthState = {
  status: Status;
  user: CurrentUser | null;
  /** The user's own branch. */
  homeBranch: BranchInfo | null;
  /** May see every branch (super administrator): the topbar shows a branch switcher. */
  allBranches: boolean;
  /** Branch currently shown: own branch, or the switcher's choice (null = every branch). */
  branchId: string | null;
  setBranchId: (id: string | null) => void;
  permissions: ReadonlySet<string>;
  sessionExpiresAt: string | null;
  /** Set when the server ended the session (shown on the login page). */
  endedReason: "session_ended" | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<MeResponse | null>(null);
  const [endedReason, setEndedReason] = useState<"session_ended" | null>(null);
  const [selected, setSelected] = useState<string | null>(() => readBranch());

  const loadMe = useCallback(async () => {
    api.setBranch(null); // the header is only for users who may switch (set below)
    const data = await api.get<MeResponse>("/auth/me");
    let chosen: string | null = null;
    const remembered = readBranch();
    if (data.all_branches && remembered) {
      // the remembered branch may have been deleted meanwhile: then show every branch
      const list = await api.get<BranchInfo[]>("/branches").catch(() => [] as BranchInfo[]);
      chosen = list.some((b) => b.id === remembered) ? remembered : null;
      if (!chosen) writeBranch(null);
    }
    api.setBranch(chosen);
    setSelected(chosen);
    setMe(data);
    setStatus("authenticated");
  }, []);

  // Restore the session after a page reload (refresh cookie -> new access token).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await api.refresh();
        if (cancelled) return;
        if (token) await loadMe();
        else setStatus("anonymous");
      } catch {
        if (!cancelled) setStatus("anonymous");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  useEffect(() => {
    api.onSessionEnded(() => {
      setMe(null);
      setEndedReason("session_ended");
      setStatus("anonymous");
    });
    return () => api.onSessionEnded(null);
  }, []);

  const login = useCallback(
    async (username: string, password: string) => {
      await api.login(username, password);
      setEndedReason(null);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setMe(null);
      setStatus("anonymous");
    }
  }, []);

  const setBranchId = useCallback((id: string | null) => {
    writeBranch(id);
    api.setBranch(id);
    setSelected(id);
  }, []);

  const value = useMemo<AuthState>(() => {
    const permissions = new Set(me?.permissions ?? []);
    const allBranches = me?.all_branches ?? false;
    return {
      status,
      user: me?.user ?? null,
      homeBranch: me?.branch ?? null,
      allBranches,
      branchId: allBranches ? selected : (me?.branch.id ?? null),
      setBranchId,
      permissions,
      sessionExpiresAt: me?.session.expires_at ?? null,
      endedReason,
      login,
      logout,
      can: (p: string) => permissions.has(p),
    };
  }, [status, me, endedReason, login, logout, selected, setBranchId]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

const BRANCH_KEY = "attendance.branch";

/** Remembered switcher choice (per browser). Storage may be unavailable: then "all branches". */
function readBranch(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(BRANCH_KEY);
  } catch {
    return null;
  }
}

function writeBranch(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(BRANCH_KEY, id);
    else window.localStorage.removeItem(BRANCH_KEY);
  } catch {
    /* private mode: the choice lasts until reload */
  }
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
