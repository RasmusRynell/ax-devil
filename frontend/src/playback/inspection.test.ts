// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { fullSample } from "../present/fixtures";
import type { LaneFrame } from "./contract";
import { Inspections } from "./inspection";
import type { ClientMessage, FieldWire, InspectionWire, ServerMessage } from "./protocol";

const SELECTION = { lane: 0, source: 0, entity: "7" };
const withCar = fullSample([
  { id: "7", class: "car", score: 0.82, confidence: 0.9, box: [0.1, 0.1, 0.3, 0.3] },
  { id: "9", class: "human", box: [0.5, 0.5, 0.6, 0.7] },
]);
const withoutCar = fullSample([{ id: "9", class: "human", box: [0.5, 0.5, 0.6, 0.7] }]);

/** What a lane draws: only the parts the inspector reads are real. */
const frame = (sample: number, columns = withCar, dataRevision = 1, source = 0): LaneFrame =>
  ({ source, dataRevision, sample, columns }) as LaneFrame;

const CLASS: FieldWire = { group: "classification", path: ["class"], value: "car" };
const found = (...fields: FieldWire[]): InspectionWire => ({
  state: "Found",
  fields,
  truncated: false,
});
const reply = (
  sample: number,
  inspection: InspectionWire,
  dataRevision = 1,
): Extract<ServerMessage, { type: "Inspected" }> => ({
  type: "Inspected",
  source: 0,
  data_revision: dataRevision,
  sample,
  entity: "7",
  inspection,
});

function setup() {
  const sent: ClientMessage[] = [];
  const inspections = new Inspections((message) => sent.push(message));
  const samples = () => sent.map((m) => (m.type === "Inspect" ? m.sample : -1));
  return { inspections, sent, samples };
}

describe("inspections", () => {
  it("shows the summary of the shown sample at once and the fields when the reply comes", () => {
    const { inspections, sent } = setup();
    inspections.follow([frame(3)]);
    expect(inspections.select(SELECTION)).toBe(true);
    expect(sent).toEqual([
      { type: "Inspect", source: 0, data_revision: 1, sample: 3, entity: "7" },
    ]);
    expect(inspections.view).toMatchObject({
      state: "loading",
      selection: SELECTION,
      summary: { entity: "7", className: "car", score: 0.82, confidence: 0.9 },
      fields: [],
    });
    // Drawing the same sample again, as every refresh does, changes nothing.
    const loading = inspections.view;
    expect(inspections.follow([frame(3)])).toBe(false);
    expect(inspections.view).toBe(loading);

    expect(inspections.receive(reply(3, found(CLASS)))).toBe(true);
    expect(inspections.view).toMatchObject({ state: "ready", fields: [CLASS] });
    expect(sent).toHaveLength(1);
  });

  it("asks for each newly shown sample at once, so a reply that never comes holds nothing up", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    inspections.follow([frame(2)]);
    inspections.follow([frame(3)]);
    expect(samples()).toEqual([1, 2, 3]);
    // A late reply for a sample no longer shown is kept but not shown.
    expect(inspections.receive(reply(1, found(CLASS)))).toBe(false);
    expect(inspections.view.state).toBe("loading");
    inspections.receive(reply(3, found({ ...CLASS, value: "bus" })));
    expect(inspections.view).toMatchObject({ state: "ready", fields: [{ value: "bus" }] });
    inspections.follow([frame(1)]);
    expect(inspections.view.state).toBe("ready");
    expect(samples()).toEqual([1, 2, 3]);
  });

  it("keeps the last fields under loading until the shown sample's reply replaces them", () => {
    const { inspections } = setup();
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    inspections.receive(reply(1, found(CLASS)));
    inspections.follow([frame(2)]);
    expect(inspections.view).toMatchObject({ state: "loading", fields: [CLASS] });
    // Another object has none of them.
    inspections.select({ ...SELECTION, entity: "9" });
    expect(inspections.view).toMatchObject({ state: "loading", fields: [] });
  });

  it("answers from the last 16 replies without asking", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    for (let sample = 0; sample < 17; sample++) {
      inspections.follow([frame(sample)]);
      inspections.receive(reply(sample, found(CLASS)));
    }
    expect(samples()).toHaveLength(17);
    inspections.follow([frame(16)]);
    expect(inspections.view.state).toBe("ready");
    expect(samples()).toHaveLength(17);
    // The first reply was dropped to make room for the seventeenth.
    inspections.follow([frame(0)]);
    expect(samples()).toHaveLength(18);
    expect(inspections.view.state).toBe("loading");
  });

  it("ignores a reply of a data revision the source has left", () => {
    const { inspections, sent } = setup();
    const revisions = () => sent.map((m) => (m.type === "Inspect" ? m.data_revision : -1));
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.follow([frame(0, withCar, 2)]);
    expect(revisions()).toEqual([1, 2]);
    expect(inspections.receive(reply(0, found(CLASS), 1))).toBe(false);
    expect(inspections.view.state).toBe("loading");
    inspections.receive(reply(0, found(CLASS), 2));
    expect(inspections.view.state).toBe("ready");
  });

  it("keeps the selection when a frame has no such object, and finds the object again", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0, withoutCar)]);
    inspections.select(SELECTION);
    expect(inspections.view).toEqual({ state: "absent", selection: SELECTION });
    expect(samples()).toEqual([]);
    inspections.follow([frame(1)]);
    expect(inspections.view).toMatchObject({ state: "loading", selection: SELECTION });
    inspections.receive(reply(1, { state: "Absent" }));
    expect(inspections.view.state).toBe("absent");
  });

  it("says unavailable for a sample the backend will not answer for, and asks again only when it is shown anew", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(reply(0, { state: "Unavailable" }));
    expect(inspections.view).toMatchObject({ state: "unavailable", summary: { className: "car" } });
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0]);
    inspections.follow([frame(1)]);
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 1, 0]);
    // The sample held anew, as after the viewer's set of samples changed, is asked for again.
    inspections.receive(reply(0, { state: "Unavailable" }));
    expect(inspections.follow([frame(0, fullSample([{ id: "7", class: "car" }]))])).toBe(true);
    expect(samples()).toEqual([0, 1, 0, 0]);
  });

  it("asks again for a sample whose reply was dropped, when it is shown again or has waited too long", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    // Another frame shows no such object, so nothing else is asked; the reply for sample 0 never comes.
    inspections.follow([frame(1, withoutCar)]);
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 0]);
    // Drawn again and again with no reply: asked once more, not at every draw.
    for (let draw = 0; draw < 130; draw++) inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 0, 0]);
    inspections.receive(reply(0, found(CLASS)));
    for (let draw = 0; draw < 130; draw++) inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 0, 0]);
  });

  it("keeps nothing of another source's object that has the same id", () => {
    const { inspections, sent } = setup();
    inspections.follow([frame(0), frame(0, withCar, 1, 1)]);
    inspections.select(SELECTION);
    inspections.receive(reply(0, found(CLASS)));
    inspections.select({ lane: 1, source: 1, entity: "7" });
    expect(inspections.view).toMatchObject({
      state: "loading",
      selection: { source: 1 },
      fields: [],
    });
    const asked = sent.at(-1);
    expect(asked).toMatchObject({ type: "Inspect", source: 1, entity: "7" });
    // The first source's reply is a different request.
    inspections.receive(reply(0, found({ ...CLASS, value: "bus" })));
    expect(inspections.view).toMatchObject({ state: "loading", fields: [] });
    inspections.receive({ ...reply(0, { state: "Unavailable" }), source: 1 });
    expect(inspections.view).toMatchObject({ state: "unavailable", fields: [] });
  });

  it("inspects the sample of the lane the object was clicked in", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(5), frame(8)]);
    inspections.select({ lane: 1, source: 0, entity: "7" });
    expect(samples()).toEqual([8]);
  });

  it("asks again on a new connection, once its data has a frame drawn", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.reconnect();
    expect(samples()).toEqual([0]);
    expect(inspections.view.state).toBe("loading");
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 0]);
    inspections.receive(reply(0, found(CLASS)));
    inspections.reconnect();
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 0, 0]);
  });

  it("clears the selection", () => {
    const { inspections } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    expect(inspections.select(null)).toBe(true);
    expect(inspections.view).toEqual({ state: "none" });
    expect(inspections.select(null)).toBe(false);
  });
});
