import { afterEach, expect, it, vi } from "vite-plus/test";
import { ask } from "./ask";
import type { ErrorWire } from "./generated/api";

afterEach(() => vi.unstubAllGlobals());

const answers = (response: () => unknown) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );

it("answers the value, or the failure the backend sent, or an Internal one when it sent none", async () => {
  answers(() => ({ ok: true, status: 200, json: async () => [1] }));
  expect(await ask("/api/x")).toEqual({ ok: true, value: [1] });

  answers(() => ({ ok: true, status: 204, json: async () => Promise.reject(new Error("empty")) }));
  expect(await ask("/api/x", { method: "POST" })).toEqual({ ok: true, value: undefined });

  const sent: ErrorWire = { code: "CameraFailed", params: { camera: "Unreachable" } };
  answers(() => ({ ok: false, status: 502, json: async () => sent }));
  expect(await ask("/api/x")).toEqual({ ok: false, error: sent });

  answers(() => ({ ok: false, status: 502, json: async () => Promise.reject(new Error("html")) }));
  expect(await ask("/api/x")).toEqual({
    ok: false,
    error: { code: "Internal", params: {}, reason: "the backend answered 502" },
  });

  answers(() => Promise.reject(new TypeError("Failed to fetch")));
  expect(await ask("/api/x")).toEqual({
    ok: false,
    error: { code: "Internal", params: {}, reason: "Failed to fetch" },
  });

  answers(() => Promise.reject(new DOMException("aborted", "AbortError")));
  await expect(ask("/api/x")).rejects.toThrow("aborted");
});
