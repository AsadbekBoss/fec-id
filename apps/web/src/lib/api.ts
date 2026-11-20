/**
 * API client for the Core API (API_CONTRACTS.md §1.2, §2.1).
 *
 * Token handling:
 * - The access token lives ONLY in memory (this module). It is never stored in localStorage or
 *   sessionStorage, so an XSS bug cannot read it from storage, and it is gone on page reload.
 * - The refresh token is an HttpOnly cookie that JavaScript never sees. A reload calls
 *   /auth/refresh to get a new access token.
 * - Refreshes are single-flight: concurrent 401s (or React StrictMode's double effects) share
 *   ONE refresh request. Two parallel refreshes would trip the server's token-reuse detection
 *   and sign the user out.
 */
import { env } from "@/lib/env";

export type ProblemError = { field: string; code: string; message: string };

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly errors: ProblemError[] = [],
    public readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Network failure / server unreachable (no HTTP response). */
export class NetworkError extends Error {
  constructor() {
    super("Network error");
    this.name = "NetworkError";
  }
}

export type TokenResponse = {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: CurrentUser;
};

export type CurrentUser = {
  id: string;
  username: string;
  email: string | null;
  role: "SUPER_ADMIN" | "ADMIN" | "VIEWER";
  branch_id: string;
  is_active: boolean;
  locale: string;
  last_login_at: string | null;
  created_at: string;
};

export type MeResponse = {
  user: CurrentUser;
  permissions: string[];
  session: { id: string; created_at: string; expires_at: string };
  /** The user's own branch. */
  branch: { id: string; name: string };
  /** May see every branch and switch between them (super administrator). */
  all_branches: boolean;
};

type Fetch = typeof fetch;

export function apiUrl(path: string): string {
  return `${env.apiBaseUrl.replace(/\/$/, "")}/api/v1${path}`;
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: { code?: string; detail?: string; title?: string; errors?: ProblemError[] } = {};
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const retry = res.headers.get("retry-after");
  return new ApiError(
    res.status,
    body.code ?? `HTTP_${res.status}`,
    body.detail ?? body.title ?? res.statusText,
    body.errors ?? [],
    retry ? Number(retry) : null,
  );
}

export class ApiClient {
  private accessToken: string | null = null;
  private refreshing: Promise<string | null> | null = null;
  private sessionEndedListener: (() => void) | null = null;
  /** Branch selected in the switcher (sent as X-Branch-Id); null = every branch. */
  private branchId: string | null = null;

  constructor(private readonly fetchImpl: Fetch = (...a) => fetch(...a)) {}

  setAccessToken(token: string | null): void {
    this.accessToken = token;
  }

  setBranch(id: string | null): void {
    this.branchId = id;
  }

  hasAccessToken(): boolean {
    return this.accessToken !== null;
  }

  onSessionEnded(listener: (() => void) | null): void {
    this.sessionEndedListener = listener;
  }

  private async raw(path: string, init: RequestInit): Promise<Response> {
    try {
      return await this.fetchImpl(apiUrl(path), { credentials: "include", ...init });
    } catch {
      throw new NetworkError();
    }
  }

  async login(username: string, password: string): Promise<TokenResponse> {
    const res = await this.raw("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) throw await toApiError(res);
    const data = (await res.json()) as TokenResponse;
    this.accessToken = data.access_token;
    return data;
  }

  /** Exchanges the HttpOnly refresh cookie for a new access token. Single-flight. */
  refresh(): Promise<string | null> {
    if (!this.refreshing) {
      this.refreshing = (async () => {
        try {
          const res = await this.raw("/auth/refresh", {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          });
          if (!res.ok) {
            this.accessToken = null;
            return null;
          }
          const data = (await res.json()) as TokenResponse;
          this.accessToken = data.access_token;
          return data.access_token;
        } finally {
          this.refreshing = null;
        }
      })();
    }
    return this.refreshing;
  }

  async logout(): Promise<void> {
    try {
      if (this.accessToken) {
        await this.raw("/auth/logout", {
          method: "POST",
          headers: { Authorization: `Bearer ${this.accessToken}` },
        });
      }
    } finally {
      this.accessToken = null;
    }
  }

  /**
   * Authenticated JSON request. An expired access token triggers one refresh and one retry.
   * A session that ended (logout elsewhere, password change, deactivation) notifies the
   * listener, which sends the user to the login page.
   */
  async request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
    const res = await this.authorized(path, init);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /** Authenticated request returning the raw successful Response (non-2xx throws ApiError). */
  private async authorized(path: string, init: RequestInit & { json?: unknown } = {}): Promise<Response> {
    const { json, headers, ...rest } = init;
    const send = () =>
      this.raw(path, {
        ...rest,
        headers: {
          ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {}),
          ...(this.branchId ? { "X-Branch-Id": this.branchId } : {}),
          ...headers,
        },
        body: json !== undefined ? JSON.stringify(json) : rest.body,
      });

    let res = await send();
    if (res.status === 401) {
      let err = await toApiError(res);
      if (err.code === "TOKEN_EXPIRED" || err.code === "NOT_AUTHENTICATED") {
        const token = await this.refresh();
        if (token) {
          res = await send();
          if (res.status !== 401) {
            if (!res.ok) throw await toApiError(res);
            return res;
          }
          err = await toApiError(res);
        }
      }
      this.accessToken = null;
      this.sessionEndedListener?.();
      throw err;
    }
    if (!res.ok) throw await toApiError(res);
    return res;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  /**
   * Authenticated streaming request (live camera preview): the caller reads `response.body`.
   * The token travels in the Authorization header, never in the URL.
   */
  async stream(path: string, signal: AbortSignal): Promise<Response> {
    return this.authorized(path, { signal });
  }

  /** Authenticated binary download (photos, signed consent forms). */
  async blob(path: string): Promise<Blob> {
    return (await this.authorized(path)).blob();
  }

  /**
   * Multipart upload of one file under form field `file`. The browser sets the multipart
   * boundary itself, so Content-Type must not be set here.
   */
  async upload<T>(path: string, file: File, method: "PUT" | "POST" = "PUT"): Promise<T> {
    const form = new FormData();
    form.append("file", file);
    const res = await this.authorized(path, { method, body: form });
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
}

/** The app-wide client instance. */
export const api = new ApiClient();
