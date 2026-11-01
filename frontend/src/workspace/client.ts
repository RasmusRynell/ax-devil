import type { AddItemsRequest, AddPlaylistRequest, ApiError, HandlersInfo } from "../generated/api";

const JSON_HEADERS = { "Content-Type": "application/json" };

/** What a request that can be refused came back with: its value, or why the backend refused it. */
export type Answer<T> = { ok: true; value: T } | { ok: false; refusal: ApiError };

/** Why the backend refused a request: its own reason and location, else just the status. */
export async function refusalOf(response: Response): Promise<ApiError> {
  const error = (await response.json().catch(() => null)) as ApiError | null;
  return error ?? { reason: `the backend answered ${response.status}`, location: "" };
}

/** Whether this is the error a `fetch` raises when its signal aborts it. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Sends a request and reads the answer; an unreachable backend is a refusal too. An abort stays an error. */
export async function ask<T>(url: string, init: RequestInit): Promise<Answer<T>> {
  try {
    const response = await fetch(url, init);
    if (!response.ok) return { ok: false, refusal: await refusalOf(response) };
    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    if (isAbort(error)) throw error;
    return {
      ok: false,
      refusal: { reason: error instanceof Error ? error.message : String(error), location: "" },
    };
  }
}

/**
 * Adds videos to the workspace and asks for a viewer on each. Resolves to why the backend refused them, else `null`;
 * the backend adds all of them or none.
 */
export async function addVideos(paths: readonly string[]): Promise<string | null> {
  const request: AddItemsRequest = {
    items: paths.map((path) => ({ type: "Video", path, overlays: [] })),
    open: true,
  };
  const response = await fetch("/api/workspace/items", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(request),
  });
  if (response.ok) return null;
  const error = await refusalOf(response);
  return error.location ? `${error.reason} (${error.location})` : error.reason;
}

/** The decoders and playlist resolvers the backend knows, with the form each resolver takes. */
export function fetchHandlers(signal: AbortSignal): Promise<Answer<HandlersInfo>> {
  return ask<HandlersInfo>("/api/handlers", { signal });
}

/**
 * Resolves a playlist and adds it to the workspace, open. Resolves to why the backend refused it, else `null`;
 * nothing is added on a refusal. Aborting the signal stops the resolving.
 */
export async function addPlaylist(
  request: AddPlaylistRequest,
  signal: AbortSignal,
): Promise<ApiError | null> {
  const answer = await ask<unknown>("/api/workspace/playlists", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(request),
    signal,
  });
  return answer.ok ? null : answer.refusal;
}
