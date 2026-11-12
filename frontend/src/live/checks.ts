/**
 * Whether the login saved with a stream gets into its camera. The backend answers from the stream's own resolved
 * login and sends nothing to a host the user has not started or confirmed (no content then), and asks a camera at
 * most once in a while, so asking again is cheap.
 */

import { useEffect, useRef, useState } from "react";
import { ask } from "../client/ask";
import type { NetworkRow } from "../generated/api";
import type { Camera } from "./cameras";

/** What signing in with each stream's own login found, by item; a stream the backend knows nothing of has none. */
export type Checks = ReadonlyMap<string, NetworkRow>;

const NONE: Checks = new Map();

/** The row of the stream's check; none when the backend sent no content or refused. */
const check = (item: string): Promise<NetworkRow | null> =>
  ask<NetworkRow | undefined>("/api/live/check", { json: { item } }).then(
    (answer) => (answer.ok && answer.value) || null,
  );

/**
 * Checks each resolved stream of `cameras` that has a recipe (so its seat may), when `enabled`: again when it is
 * resolved anew, started or stopped, and when a new scan starts (`scan`), which is how Rescan signs in again.
 */
export function useLoginChecks(cameras: readonly Camera[], enabled: boolean, scan: number): Checks {
  const [checks, setChecks] = useState<Checks>(NONE);
  const asked = useRef(new Map<string, string>());
  useEffect(() => {
    if (!enabled) return;
    // A stream resolving anew or without a recipe has no check: an answer still on its way is out of date.
    const streams = cameras
      .flatMap(({ streams }) => streams)
      .filter((stream) => stream.state === "ready" && stream.recipe);
    const ids = new Set(streams.map(({ id }) => id));
    for (const id of asked.current.keys()) if (!ids.has(id)) asked.current.delete(id);
    setChecks((now) =>
      [...now.keys()].every((id) => ids.has(id))
        ? now
        : new Map([...now].filter(([id]) => ids.has(id))),
    );
    for (const stream of streams) {
      const key = `${stream.generation}:${stream.open}:${scan}`;
      if (asked.current.get(stream.id) === key) continue;
      asked.current.set(stream.id, key);
      void check(stream.id).then((row) => {
        // An answer to a question since asked again is out of date.
        if (asked.current.get(stream.id) !== key) return;
        setChecks((now) => {
          const next = new Map(now);
          if (row) next.set(stream.id, row);
          else next.delete(stream.id);
          return next;
        });
      });
    }
  }, [cameras, enabled, scan]);
  return enabled ? checks : NONE;
}
