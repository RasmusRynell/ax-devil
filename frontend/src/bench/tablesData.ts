/** What the tables benchmark shows: a lane that is ready, a file of made-up objects behind the File scope, and frames of objects. */

import type { FileRowWire, FileSortWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import type { FileScope } from "../playback/fileScope";
import type { LaneStatusWire } from "../playback/protocol";
import { catalogNamed, laneRing } from "./synthetic";

export const RING = 6;
const CLASSES = ["car", "human", "head", "bus", "dog", "truck"];

export const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};

const fileRow = (index: number): FileRowWire => {
  const first = (index * 37) % 900;
  return {
    id: `track-${index * 7919}`,
    classes: [CLASSES[index % CLASSES.length]],
    events: [],
    first_frame: first,
    last_frame: first + 50,
    presence: [
      [first, first + 20],
      [first + 30, first + 50],
    ],
  };
};

type FileScopeView = ReturnType<FileScope["view"]>;

/** The File scope client of a lane whose file has `total` objects; a page is answered a task after it is asked for. */
export class FakeFiles {
  readonly #listeners = new Set<() => void>();
  readonly #total: number;
  #view: FileScopeView = { pending: false, page: null, unavailable: null };

  constructor(total: number) {
    this.#total = total;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };
  view = (_lane: number): FileScopeView => this.#view;
  request = (_lane: number, _text: string, _sort: FileSortWire, offset: number, limit: number) => {
    const rows = Array.from({ length: Math.min(limit, this.#total - offset) }, (_, k) =>
      fileRow(offset + k),
    );
    setTimeout(() => {
      this.#view = {
        pending: false,
        page: { total: this.#total, matched: this.#total, offset, rows },
        unavailable: null,
      };
      this.#listeners.forEach((listener) => listener());
    }, 0);
  };
  release = (_lane: number) => {};
}

const rings = new Map<number, LaneFrame[]>();
/** `RING` frames of `objects` objects each, made once. */
export function ringOf(objects: number): LaneFrame[] {
  let ring = rings.get(objects);
  if (!ring) {
    ring = laneRing(catalogNamed("standard"), 1, objects, RING).map((step) => step[0]);
    rings.set(objects, ring);
  }
  return ring;
}
