import { useEffect } from "react";
import type { FileScope } from "./fileScope";

/**
 * Gives the viewer its own frames, and so the whole file's lists, whenever the seat is not following a presenter. A
 * window that follows is told so by `PlaybackSession.follow`; nothing else says it stopped, as when a Guest changes
 * its view without touching playback.
 */
export function useFileScopeOwner(
  session: { readonly files: Pick<FileScope, "setOwner"> } | null,
  following: boolean,
): void {
  useEffect(() => {
    if (session && !following) session.files.setOwner(true);
  }, [session, following]);
}
