// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { LaneFrame } from "./contract";
import { FrameFields } from "./frameFields";
import type { ClientMessage, ServerMessage } from "./protocol";

const frame = (sample: number, columns: unknown = {}) =>
  ({ source: 0, dataRevision: 1, sample, columns }) as unknown as LaneFrame;

const answer = (
  sample: number,
  objects: Extract<ServerMessage, { type: "InspectedFields" }>["objects"],
): Extract<ServerMessage, { type: "InspectedFields" }> => ({
  type: "InspectedFields",
  source: 0,
  data_revision: 1,
  sample,
  paths: ["id"],
  objects,
});

describe("the leaves of a frame's objects", () => {
  it("are asked once per sample and read for exactly that sample, with integers exact", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const paths = ["id"];

    fields.ask(frame(1), paths);
    fields.ask(frame(1), paths);
    expect(sent).toHaveLength(1);
    expect(fields.view(frame(1), paths).state).toBe("pending");

    fields.receive(
      answer(1, [
        {
          entity: "7",
          items: [
            { key: "debug.id", value: { kind: "int", value: "9007199254740993" } },
            { key: "debug.id", value: { kind: "bool", value: true } },
            { key: "debug.note", value: { kind: "null" } },
          ],
        },
      ]),
    );
    const view = fields.view(frame(1), paths);
    expect(view.state === "ready" && view.leaves.get("7")).toEqual({
      "debug.id": [9007199254740993n, "true"],
      "debug.note": ["null"],
    });

    // The next sample is awaited, not given the last one's leaves, and a late answer of the last one is not its.
    fields.ask(frame(2), paths);
    expect(sent).toHaveLength(2);
    expect(fields.view(frame(2), paths).state).toBe("pending");
    fields.receive(answer(1, []));
    expect(fields.view(frame(2), paths).state).toBe("pending");
  });

  it("are not asked again for a sample the backend does not answer for until the viewer holds it anew", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const held = {};

    fields.ask(frame(1, held), ["id"]);
    fields.receive(answer(1, null));
    expect(fields.view(frame(1, held), ["id"]).state).toBe("unavailable");
    fields.ask(frame(1, held), ["id"]);
    expect(sent).toHaveLength(1);
    fields.ask(frame(1, {}), ["id"]);
    expect(sent).toHaveLength(2);
  });

  it("are asked again when the answer never came", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const held = frame(1);

    for (let asks = 0; asks < 100; asks++) fields.ask(held, ["id"]);
    expect(sent).toHaveLength(1);
    for (let asks = 0; asks < 100; asks++) fields.ask(held, ["id"]);
    expect(sent).toHaveLength(2);
  });
});
