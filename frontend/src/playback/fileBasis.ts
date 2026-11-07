/** What a lane's whole-file answers (lists, appearances) share: they are asked again when it changes. */

import type { LaneState } from "./contract";
import type { LaneSet, ReadyStatus } from "./lanes";

/** How long to wait before asking again after a follower's answer to a viewer that believes it owns its frames. */
export const FOLLOWING_RETRY_MS = 1000;
/** How often that is tried: the backend learns of a viewer's authority a moment after the viewer does. */
export const FOLLOWING_RETRIES = 3;

/** The lane's Ready status, if it is ready. */
export const readyOf = (state: LaneState | undefined): ReadyStatus | undefined =>
  state && state.status !== "plain" && state.status.status === "Ready" ? state.status : undefined;

/** What the backend's answer depends on. */
type Basis = { data: number; policy: number; reading: boolean; owner: boolean };

/** What `lane`'s answers depend on now, for a viewer that `owner`s its frames or follows. */
export function basisOf(lanes: LaneSet, lane: number, owner: boolean): Basis {
  const state = lanes.states[lane];
  const ready = readyOf(state);
  return {
    data: ready?.data_revision ?? -1,
    policy: state?.policyRevision ?? 0,
    reading: ready?.ingest.state === "Reading",
    owner,
  };
}
