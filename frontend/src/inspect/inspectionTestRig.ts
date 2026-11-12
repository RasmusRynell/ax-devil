import { fullSample } from "../present/fixtures";
import type { LaneFrame } from "../lanes/contract";
import { Inspections } from "./inspection";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "../stream/protocol";

/** Shared inspection scenario data. */
export const SELECTION = { lane: 0, source: 0, entity: "7" };
/** Shared inspection scenario data. */
export const withCar = fullSample([
  {
    id: "7",
    class: "car",
    score: 0.82,
    confidence: 0.9,
    motion: "moving",
    mask: [0b01],
    box: [0.1, 0.1, 0.3, 0.3],
  },
  { id: "9", class: "human", box: [0.5, 0.5, 0.6, 0.7] },
]);
/** Shared inspection scenario data. */
export const withoutCar = fullSample([{ id: "9", class: "human", box: [0.5, 0.5, 0.6, 0.7] }]);

/** A displayed sample. */
/** Shared inspection scenario data. */
export const frame = (
  sample: number,
  columns = withCar,
  dataRevision = 1,
  source = 0,
  style: Partial<LaneFrame> = {},
): LaneFrame =>
  ({
    source,
    dataRevision,
    sample,
    columns,
    styleRevision: 0,
    classMask: { enabled: new Uint32Array(1), options: 0 },
    filter: null,
    ...style,
  }) as LaneFrame;

/** Shared inspection scenario data. */
export const CLASS: FieldWire = {
  group: "classification",
  path: ["class"],
  value: { kind: "text", value: "car" },
};
/** Shared inspection scenario data. */
export const found = (...fields: FieldWire[]): InspectionWire => ({
  state: "Found",
  fields,
  truncated: false,
});
const replyOf = (
  request: number,
  sample: number,
  inspection: InspectionWire,
  dataRevision = 1,
): Extract<ServerMessage, { type: "Inspected" }> => ({
  type: "Inspected",
  request,
  source: 0,
  data_revision: dataRevision,
  sample,
  entity: "7",
  inspection,
});

/** One real inspection owner with a captured transport and controlled clock. */
export function setup(after: (run: () => void, ms: number) => () => void = () => () => {}) {
  const sent: ClientMessage[] = [];
  const time = { now: 0 };
  const inspections = new Inspections(
    (message) => sent.push(message),
    () => time.now,
    undefined,
    after,
  );
  const samples = () => sent.flatMap((m) => (m.type === "Inspect" ? [m.sample] : []));
  const reply = (sample: number, inspection: InspectionWire, revision = 1) => {
    const asked = sent
      .filter((m) => m.type === "Inspect" && m.sample === sample && m.data_revision === revision)
      .at(-1);
    if (asked?.type !== "Inspect") throw Error("inspection request");
    return replyOf(asked.request, sample, inspection, revision);
  };
  return { inspections, sent, samples, time, reply };
}
