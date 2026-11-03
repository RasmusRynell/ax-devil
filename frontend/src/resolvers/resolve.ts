import type { ResolvedInfo, ResolveRequest } from "../generated/api";
import { ask, type Answer } from "./handlers";
import type { Settings } from "./scan";

/** Resolves a resolver's playlists for the settings; the answer names the item to add. Aborting the signal cancels the resolve. */
export function resolvePlaylists(
  resolver: string,
  settings: Settings,
  signal: AbortSignal,
): Promise<Answer<ResolvedInfo>> {
  const request: ResolveRequest = { settings };
  return ask<ResolvedInfo>(`/api/resolvers/${encodeURIComponent(resolver)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
}
