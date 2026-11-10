"use client";

/**
 * Authentication state for the whole app. The server enforces every permission. The UI only
 * uses permissions to hide actions the user cannot perform anyway.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, type CurrentUser, type MeResponse } from "@/lib/api";

type Status = "loading" | "authenticated" | "anonymous";

type AuthState = {
  status: Status;
  user: CurrentUser | null;
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

  const loadMe = useCallback(async () => {
    const data = await api.get<MeResponse>("/auth/me");
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

  const value = useMemo<AuthState>(() => {
    const permissions = new Set(me?.permissions ?? []);
    return {
      status,
      user: me?.user ?? null,
      permissions,
      sessionExpiresAt: me?.session.expires_at ?? null,
      endedReason,
      login,
      logout,
      can: (p: string) => permissions.has(p),
    };
  }, [status, me, endedReason, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
