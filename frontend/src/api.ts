import { credential } from "./access";

/**
 * `fetch` for this page's own backend: `path` stays relative, and a server password held by this tab goes out as
 * `Authorization: Bearer`.
 */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const held = credential();
  if (held?.kind !== "password") return fetch(path, init);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${held.password}`);
  return fetch(path, { ...init, headers });
}
