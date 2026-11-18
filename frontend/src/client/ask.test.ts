import { afterEach, expect, it, vi } from "vite-plus/test";
import { ask } from "./ask";
import type { ErrorWire } from "../generated/api";

afterEach(() => vi.unstubAllGlobals());

const answers = (response: () => unknown) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );

it("answers the value, or the failure the backend sent, or an Internal one when it sent none", async () => {
  answers(() => ({ ok: true, status: 200, json: async () => [1] }));
  expect(await ask("GET /api/sessions")).toEqual({ ok: true, value: [1] });

  answers(() => ({ ok: true, status: 204, json: async () => Promise.reject(new Error("empty")) }));
  expect(await ask("POST /api/network/cancel")).toEqual({ ok: true, value: null });

  const sent: ErrorWire = { code: "CameraFailed", params: { camera: "Unreachable" } };
  answers(() => ({ ok: false, status: 502, json: async () => sent }));
  expect(await ask("GET /api/sessions")).toEqual({ ok: false, error: sent });

  answers(() => ({ ok: false, status: 502, json: async () => Promise.reject(new Error("html")) }));
  expect(await ask("GET /api/sessions")).toEqual({
    ok: false,
    error: { code: "Internal", params: {}, reason: "the backend answered 502" },
  });

  answers(() => Promise.reject(new TypeError("Failed to fetch")));
  expect(await ask("GET /api/sessions")).toEqual({
    ok: false,
    error: { code: "Internal", params: {}, reason: "Failed to fetch" },
  });

  answers(() => Promise.reject(new DOMException("aborted", "AbortError")));
  await expect(ask("GET /api/sessions")).rejects.toThrow("aborted");
});

it("sends a GET's request as its query and any other method's as JSON, with path parameters filled", async () => {
  const sent = vi.fn(async () => ({ ok: true, status: 200, json: async () => null }));
  vi.stubGlobal("fetch", sent);
  await ask("GET /api/files/folder", { request: { path: "/home/a b/é&" } });
  await ask("GET /api/files/folder", { request: { path: undefined } });
  await ask("POST /api/resolvers/{id}/scan", {
    params: { id: "a/b" },
    request: { field: "f", settings: {} },
  });
  const [folder, root, scan] = sent.mock.calls as unknown as [string, RequestInit][];
  expect(folder[0]).toBe("/api/files/folder?path=%2Fhome%2Fa+b%2F%C3%A9%26");
  expect(root[0]).toBe("/api/files/folder");
  expect(scan[0]).toBe("/api/resolvers/a%2Fb/scan");
  expect(scan[1]).toMatchObject({ method: "POST", body: '{"field":"f","settings":{}}' });
});
