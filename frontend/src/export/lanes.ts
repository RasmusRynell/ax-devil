/** Which lanes an export can draw, and the wire form of each as the viewer holds it now. */

import type { ExportLaneWire } from "../generated/api";
import type { LaneState } from "../playback/contract";
import { styleOf } from "../playback/laneStyle";

/** A lane the export dialog offers. `wire` is absent while the lane cannot be drawn yet: its data or catalog is loading or failed. */
type ExportChoice = { lane: number; label: string; wire: ExportLaneWire | null };

/** The lane as the backend should draw it, from what the viewer holds of it; `null` while that is incomplete. */
function wireOf(state: LaneState, lane: number): ExportLaneWire | null {
  const { status, catalogRevision } = state;
  if (catalogRevision === undefined) return null;
  const ready = status !== "plain" && status.status === "Ready" ? status : null;
  if (state.source !== null && !ready) return null;
  return {
    lane,
    catalog: { id: state.settings.catalog, revision: catalogRevision },
    style: styleOf(state.label, state.settings, ready?.filter ?? []),
    policy_revision: state.policyRevision,
    data_revision: ready?.data_revision ?? null,
  };
}

/** The lanes the viewer shows, in the order they tile. */
export function choicesOf(lanes: readonly LaneState[]): ExportChoice[] {
  return lanes.flatMap((state, lane) =>
    state.settings.visible ? [{ lane, label: state.label, wire: wireOf(state, lane) }] : [],
  );
}
