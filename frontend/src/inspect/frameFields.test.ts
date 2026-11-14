// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { LaneFrame } from "../lanes/contract";
import { FrameFields } from "./frameFields";
import type { ClientMessage, ServerMessage } from "../stream/protocol";

const columns = {};
const frame = (sample: number, sampleColumns: unknown = columns) =>
  ({ source: 0, dataRevision: 1, sample, columns: sampleColumns }) as unknown as LaneFrame;

const answer = (
  sent: readonly ClientMessage[],
  sample: number,
  objects: Extract<ServerMessage, { type: "InspectedFields" }>["objects"],
): Extract<ServerMessage, { type: "InspectedFields" }> => {
  const asked = sent
    .filter(
      (message): message is Extract<ClientMessage, { type: "InspectFields" }> =>
        message.type === "InspectFields" && message.sample === sample,
    )
    .at(-1);
  if (!asked) throw Error("search request");
  return {
    type: "InspectedFields",
    request: asked.request,
    source: 0,
    data_revision: 1,
    sample,
    paths: asked.paths,
    objects,
  };
};

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
      answer(sent, 1, [
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
    fields.receive(answer(sent, 1, []));
    expect(fields.view(frame(2), paths).state).toBe("pending");
  });

  it("settle a refusal without loading, and recover when permission returns or the sample is held anew", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const held = {};

    fields.ask(frame(1, held), ["id"]);
    fields.receive(answer(sent, 1, null));
    expect(fields.view(frame(1, held), ["id"]).state).toBe("unavailable");
    fields.ask(frame(1, held), ["id"]);
    expect(sent).toHaveLength(1);
    fields.ask(frame(1, {}), ["id"]);
    expect(sent).toHaveLength(2);
    fields.receive(answer(sent, 1, []));
    expect(fields.view(frame(1), ["id"]).state).toBe("ready");
    fields.ask(frame(1), ["id"], false);
    expect(fields.view(frame(1), ["id"], false).state).toBe("unavailable");
    expect(sent).toHaveLength(2);
    fields.ask(frame(1), ["id"], true);
    expect(sent).toHaveLength(3);
    fields.receive(answer(sent, 1, []));
    expect(fields.view(frame(1), ["id"], true).state).toBe("ready");
  });

  it("ignore the answer of a request that was replaced", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const held = frame(1);

    for (const absent of [false, true]) {
      fields.reconnect();
      fields.ask(held, ["debug.foo"]);
      const old = answer(sent, 1, null);
      fields.receive(old);
      expect(fields.view(held, ["debug.foo"]).state).toBe("unavailable");
      fields.ask(absent ? undefined : held, absent ? ["debug.foo"] : []);
      fields.ask(held, ["debug.foo"]);
      expect(fields.view(held, ["debug.foo"]).state).toBe("pending");
      fields.receive({ ...old, objects: [] });
      expect(fields.view(held, ["debug.foo"]).state).toBe("pending");
      fields.receive(answer(sent, 1, []));
      expect(fields.view(held, ["debug.foo"]).state).toBe("ready");
    }
    fields.disconnect();
  });

  it("are refused beyond the bounds of a request, and abandoned with the connection", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message));
    const held = frame(1);

    fields.ask(held, ["id"]);
    fields.disconnect();
    expect(fields.view(held, ["id"]).state).toBe("unavailable");
    fields.reconnect();
    fields.ask(held, ["id"]);
    expect(sent).toHaveLength(2);
    for (const paths of [
      ["界".repeat(1024)],
      Array(65).fill("id") as string[],
      Array(64).fill("x".repeat(1024)) as string[],
    ]) {
      fields.ask(held, paths);
      expect(fields.view(held, paths).state).toBe("unavailable");
      expect(sent).toHaveLength(2);
    }
    fields.ask(held, ["id"]);
    expect(sent).toHaveLength(3);
    fields.receive(answer(sent, 1, []));
    expect(fields.view(held, ["id"]).state).toBe("ready");
  });
});
