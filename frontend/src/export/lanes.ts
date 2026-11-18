/** Which lanes an export can draw, and the wire form of each as the viewer holds it now. */

import type { ExportLaneWire } from "../generated/api";
import type { LaneState } from "../lanes/contract";
import { styleOf } from "../lanes/laneStyle";

/** Why a lane cannot be drawn yet: its data or catalog is still loading, or failed. */
type Cold = "loading" | "failed";

/** A lane the export dialog offers. `wire` is absent while the lane cannot be drawn yet, and `cold` then says why. */
type ExportChoice = { lane: number; label: string; wire: ExportLaneWire | null; cold: Cold | null };

/** The lane as the backend should draw it, from what the viewer holds of it; the reason while that is incomplete. */
function wireOf(state: LaneState, lane: number): ExportLaneWire | Cold {
  const { status, catalogRevision } = state;
  if (status !== "plain" && status.status === "Failed") return "failed";
  if (catalogRevision === undefined) return state.catalogProblem ? "failed" : "loading";
  const ready = status !== "plain" && status.status === "Ready" ? status : null;
  if (state.source !== null && !ready) return "loading";
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
  return lanes.flatMap((state, lane) => {
    if (!state.settings.visible) return [];
    const made = wireOf(state, lane);
    const [wire, cold] = typeof made === "string" ? [null, made] : [made, null];
    return [{ lane, label: state.label, wire, cold }];
  });
}
