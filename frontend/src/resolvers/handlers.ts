import { ask, type Answer } from "../ask";
import type { HandlersInfo } from "../generated/api";

/** The decoders and playlist resolvers the backend knows, with the form each resolver takes. */
export function fetchHandlers(signal: AbortSignal): Promise<Answer<HandlersInfo>> {
  return ask<HandlersInfo>("/api/handlers", { signal });
}
