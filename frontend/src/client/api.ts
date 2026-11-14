import type { ErrorWire } from "../generated/api";
import { credential, sendable } from "./access";
import { reasonOf } from "./errors";

/**
 * `fetch` for this page's own backend: `path` stays relative, and a server password held by this tab goes out as
 * `Authorization: Bearer`. A password the header cannot carry is not sent: the backend answers as to anyone without a
 * credential, and the Session connection reports the rejection.
 */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const held = credential();
  if (held?.kind !== "password" || !sendable(held.password)) return fetch(path, init);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${held.password}`);
  return fetch(path, { ...init, headers });
}

/** Throws why the backend refused `response`: its own reason when it sent one, else its status. */
export async function failWith(response: Response): Promise<never> {
  const error = (await response.json().catch(() => null)) as ErrorWire | null;
  throw new Error(error?.code ? reasonOf(error) : `The backend answered ${response.status}`);
}
