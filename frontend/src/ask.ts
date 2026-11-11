import { apiFetch } from "./api";
import type { ErrorWire } from "./generated/api";

/** What a request came back with: its value (`undefined` for a 204), or the failure the backend sent. */
export type Answer<T> = { ok: true; value: T } | { ok: false; error: ErrorWire };

/** What `ask` sends: a `json` body makes it a POST unless `method` says otherwise. */
export interface AskInit {
  method?: "POST" | "PATCH";
  json?: unknown;
  signal?: AbortSignal;
}

/** The failure of a backend that sent none of its own: a status without a body, or no answer at all. */
const unanswered = (reason: string): ErrorWire => ({ code: "Internal", params: {}, reason });

/**
 * Asks the backend and reads the answer as `T`. Every route answers its value or an `ErrorWire`; a backend that
 * cannot be reached, or answers something else, is an `Internal` one. An abort stays an error.
 */
export async function ask<T>(
  url: string,
  { method, json, signal }: AskInit = {},
): Promise<Answer<T>> {
  try {
    const response = await apiFetch(url, {
      method: method ?? (json === undefined ? undefined : "POST"),
      headers: json === undefined ? undefined : { "Content-Type": "application/json" },
      body: json === undefined ? undefined : JSON.stringify(json),
      signal,
    });
    if (!response.ok) {
      const error = (await response.json().catch(() => null)) as ErrorWire | null;
      return {
        ok: false,
        error: error?.code ? error : unanswered(`the backend answered ${response.status}`),
      };
    }
    return { ok: true, value: (response.status === 204 ? undefined : await response.json()) as T };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { ok: false, error: unanswered(error instanceof Error ? error.message : String(error)) };
  }
}
