import type { ResolvedInfo, ResolveRequest } from "../generated/api";
import { ask, type Answer } from "./handlers";
import type { Settings } from "./scan";

/** Resolves a resolver's playlists for the settings; the answer names the item to add. `session` is the window's Session, the only one that can add the item. Aborting the signal cancels the resolve. */
export function resolvePlaylists(
  resolver: string,
  settings: Settings,
  session: string,
  signal?: AbortSignal,
): Promise<Answer<ResolvedInfo>> {
  const request: ResolveRequest = { session, settings };
  return ask<ResolvedInfo>(`/api/resolvers/${encodeURIComponent(resolver)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
}
