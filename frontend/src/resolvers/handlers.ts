import { apiFetch } from "../api";
import type { ErrorWire, HandlersInfo } from "../generated/api";

/** What a request that can be refused came back with: its value, or why the backend refused it. */
export type Answer<T> = { ok: true; value: T } | { ok: false; refusal: ErrorWire };

/** Why the backend refused a request: the failure it sent, else just the status. */
async function refusalOf(response: Response): Promise<ErrorWire> {
  const error = (await response.json().catch(() => null)) as ErrorWire | null;
  return error?.code
    ? error
    : { code: "Internal", params: {}, reason: `the backend answered ${response.status}` };
}

/** Sends a request and reads the answer (`undefined` for a 204); an unreachable backend is a refusal too. An abort stays an error. */
export async function ask<T>(url: string, init: RequestInit): Promise<Answer<T>> {
  try {
    const response = await apiFetch(url, init);
    if (!response.ok) return { ok: false, refusal: await refusalOf(response) };
    if (response.status === 204) return { ok: true, value: undefined as T };
    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return {
      ok: false,
      refusal: {
        code: "Internal",
        params: {},
        reason: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

/** The decoders and playlist resolvers the backend knows, with the form each resolver takes. */
export function fetchHandlers(signal: AbortSignal): Promise<Answer<HandlersInfo>> {
  return ask<HandlersInfo>("/api/handlers", { signal });
}
