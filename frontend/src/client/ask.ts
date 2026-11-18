import { apiFetch } from "./api";
import type { ErrorWire } from "../generated/api";
import type { Routes } from "../generated/routes";

/** What a request came back with: its value (`null` for a 204), or the failure the backend sent. */
export type Answer<T> = { ok: true; value: T } | { ok: false; error: ErrorWire };

/** The routes `useResource` reads: those a GET asks with no query. */
export type ReadKey = {
  [K in keyof Routes]: K extends `GET ${string}`
    ? Routes[K]["request"] extends null
      ? K
      : never
    : never;
}[keyof Routes];

/**
 * What `ask` sends to the route `K`: its `request` (a JSON body, or a GET's query), the `{...}` of its path as
 * `params`, and a signal; the first two are named only when the route has them.
 */
export type AskInit<K extends keyof Routes> = (Routes[K]["request"] extends null
  ? { request?: undefined }
  : { request: Routes[K]["request"] }) &
  (Routes[K]["params"] extends null ? { params?: undefined } : { params: Routes[K]["params"] }) & {
    signal?: AbortSignal;
  };

/** `ask`'s second argument: optional only for a route that takes nothing. */
type AskArgs<K extends keyof Routes> = Routes[K]["request"] extends null
  ? Routes[K]["params"] extends null
    ? [init?: AskInit<K>]
    : [init: AskInit<K>]
  : [init: AskInit<K>];

/** The failure of a backend that sent none of its own: a status without a body, or no answer at all. */
const unanswered = (reason: string): ErrorWire => ({ code: "Internal", params: {}, reason });

/**
 * Asks the backend the route `key` (`"METHOD /path"`, as `Routes` lists) and reads the answer as its response. Every
 * route answers its value or an `ErrorWire`; a backend that cannot be reached, or answers something else, is an
 * `Internal` one. An abort stays an error.
 */
export async function ask<K extends keyof Routes>(
  key: K,
  ...[init]: AskArgs<K>
): Promise<Answer<Routes[K]["response"]>> {
  const [method, template] = key.split(" ") as [string, string];
  const { request, params, signal } = (init ?? {}) as {
    request?: object;
    params?: Record<string, string>;
    signal?: AbortSignal;
  };
  let url = template.replace(/\{(\w+)\}/g, (_, name: string) => encodeURIComponent(params![name]!));
  const body = request === undefined || method === "GET" ? undefined : JSON.stringify(request);
  if (method === "GET" && request !== undefined) {
    const query = new URLSearchParams(
      Object.entries(request).flatMap(([name, value]) =>
        value === undefined ? [] : [[name, String(value)]],
      ),
    );
    if (query.size > 0) url = `${url}?${query}`;
  }
  try {
    const response = await apiFetch(url, {
      method: method === "GET" ? undefined : method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body,
      signal,
    });
    if (!response.ok) {
      const error = (await response.json().catch(() => null)) as ErrorWire | null;
      return {
        ok: false,
        error: error?.code ? error : unanswered(`the backend answered ${response.status}`),
      };
    }
    return { ok: true, value: response.status === 204 ? null : await response.json() };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { ok: false, error: unanswered(error instanceof Error ? error.message : String(error)) };
  }
}
