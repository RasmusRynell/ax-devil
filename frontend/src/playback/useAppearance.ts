import { useEffect, useSyncExternalStore } from "react";
import type { Appearance, Appearances } from "./appearances";

/** What an object page reads of a viewer to know where its object appears: `FileScope` is one. */
export type AppearanceSource = {
  readonly appearances: Pick<Appearances, "want" | "view">;
  readonly subscribe: (listener: () => void) => () => void;
};

/**
 * Where `entity` appears on `lane`'s video, asked of the backend while the page is shown; `null` until known or when
 * the backend cannot say, as for a live stream or a viewer that follows.
 */
export function useAppearance(
  source: AppearanceSource,
  lane: number,
  entity: string,
): Appearance | null {
  // The lane is released when the page closes or moves to another lane; a new object on the same lane replaces the
  // old one and keeps what the backend holds for the lane.
  useEffect(() => () => source.appearances.want(lane, null), [source, lane]);
  useEffect(() => {
    source.appearances.want(lane, entity);
  }, [source, lane, entity]);
  const held = useSyncExternalStore(source.subscribe, () => source.appearances.view(lane));
  return held?.entity === entity ? held : null;
}
