import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, NetworkError } from "@/lib/api";

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const tokens = (token: string) => ({
  access_token: token,
  token_type: "Bearer",
  expires_in: 900,
  user: { id: "u", username: "admin", role: "ADMIN" },
});

describe("ApiClient", () => {
  it("login stores the access token in memory and sends credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, tokens("A1")));
    const client = new ApiClient(fetchMock);
    await client.login("admin", "secret-pass");
    expect(client.hasAccessToken()).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/auth\/login$/);
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body)).toEqual({ username: "admin", password: "secret-pass" });
  });

  it("never writes tokens to web storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const client = new ApiClient(vi.fn().mockResolvedValue(json(200, tokens("A1"))));
    await client.login("admin", "x");
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("maps problem details, including Retry-After", async () => {
    const client = new ApiClient(
      vi.fn().mockResolvedValue(json(429, { code: "TOO_MANY_ATTEMPTS", detail: "Slow down" }, { "Retry-After": "120" })),
    );
    const err = await client.login("a", "b").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("TOO_MANY_ATTEMPTS");
    expect((err as ApiError).retryAfter).toBe(120);
  });

  it("turns fetch failures into NetworkError", async () => {
    const client = new ApiClient(vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(client.login("a", "b")).rejects.toBeInstanceOf(NetworkError);
  });

  it("refresh is single-flight (concurrent callers share one request)", async () => {
    let resolve!: (r: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    const client = new ApiClient(fetchMock);
    const a = client.refresh();
    const b = client.refresh();
    resolve(json(200, tokens("R1")));
    expect(await a).toBe("R1");
    expect(await b).toBe("R1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Requested-With"]).toBe("XMLHttpRequest");
  });

  it("retries once after TOKEN_EXPIRED using a refreshed token", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(401, { code: "TOKEN_EXPIRED" }))
      .mockResolvedValueOnce(json(200, tokens("NEW")))
      .mockResolvedValueOnce(json(200, { ok: true }));
    const client = new ApiClient(fetchMock);
    client.setAccessToken("OLD");
    await expect(client.get("/auth/me")).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer OLD");
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe("Bearer NEW");
  });

  it("notifies when the session ended and does not retry", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(401, { code: "SESSION_ENDED" }));
    const client = new ApiClient(fetchMock);
    const ended = vi.fn();
    client.onSessionEnded(ended);
    client.setAccessToken("T");
    await expect(client.get("/auth/me")).rejects.toMatchObject({ code: "SESSION_ENDED" });
    expect(ended).toHaveBeenCalledOnce();
    expect(client.hasAccessToken()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("ends the session when refresh fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(401, { code: "TOKEN_EXPIRED" }))
      .mockResolvedValueOnce(json(401, { code: "INVALID_REFRESH_TOKEN" }));
    const client = new ApiClient(fetchMock);
    const ended = vi.fn();
    client.onSessionEnded(ended);
    client.setAccessToken("T");
    await expect(client.get("/x")).rejects.toBeInstanceOf(ApiError);
    expect(ended).toHaveBeenCalledOnce();
  });

  it("returns undefined for 204 and throws ApiError for other failures", async () => {
    const client = new ApiClient(
      vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(json(403, { code: "FORBIDDEN" })),
    );
    client.setAccessToken("T");
    await expect(client.request("/a", { method: "DELETE" })).resolves.toBeUndefined();
    await expect(client.get("/b")).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
  });

  it("logout clears the token even if the request fails", async () => {
    const client = new ApiClient(vi.fn().mockRejectedValue(new TypeError("offline")));
    client.setAccessToken("T");
    await client.logout().catch(() => undefined);
    expect(client.hasAccessToken()).toBe(false);
  });
});
