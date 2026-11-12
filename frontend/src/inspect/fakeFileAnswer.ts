// What the File scope tests share: a ready lane, a fake clock and the backend's answers.
import type { FileBody, FileScopeUnavailable } from "../generated/api";
import { LANE_SETTINGS } from "../lanes/fakeOpened";
import type { FileMessage } from "./fileRequests";
import { LaneSet } from "../lanes/lanes";
import type { LaneStatusWire } from "../stream/protocol";

/** A lane's Ready status: a Car option on by default and a Bus option off. */
export const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [
    { id: "show_car", label: "Car", default_enabled: true },
    { id: "show_bus", label: "Bus", default_enabled: false },
  ],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};

/** One lane, ready. */
export function readyLane(): LaneSet {
  const lanes = new LaneSet();
  lanes.open([{ label: "DPM", slot: "DPM", source: 0 }], LANE_SETTINGS);
  lanes.setStatus(0, READY);
  return lanes;
}

/** Calls back after `ms` of Vitest's fake timers; returns how to cancel. */
export function after(run: () => void, ms: number): () => void {
  const timer = setTimeout(run, ms);
  return () => clearTimeout(timer);
}

/** The answer to request `request` with `body`, under the lane's current revisions unless told. */
export function found(
  request: number,
  body: FileBody,
  over: { lane?: number; data_revision?: number; policy_revision?: number } = {},
): FileMessage {
  const { lane = 0, data_revision = 1, policy_revision = 1 } = over;
  return {
    type: "File",
    lane,
    request,
    answer: { state: "Found", data_revision, policy_revision, body },
  };
}

/** The answer to request `request` that the backend cannot give, and why. */
export function unavailable(request: number, why: FileScopeUnavailable, lane = 0): FileMessage {
  return { type: "File", lane, request, answer: { state: "Unavailable", unavailable: why } };
}
