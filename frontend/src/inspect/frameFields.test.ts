// @vitest-environment node
import { describe, expect, it, vi } from "vite-plus/test";
import type { LaneFrame } from "../lanes/contract";
import { FrameFields } from "./frameFields";
import type { ClientMessage, ServerMessage } from "../stream/protocol";

const after = (run: () => void, ms: number) => {
  const timer = setTimeout(run, ms);
  return () => clearTimeout(timer);
};

const frame = (sample: number, columns: unknown = {}) =>
  ({ source: 0, dataRevision: 1, sample, columns }) as unknown as LaneFrame;

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
    paths: ["id"],
    objects,
  };
};

describe("the leaves of a frame's objects", () => {
  it("are asked once per sample and read for exactly that sample, with integers exact", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message), after);
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

  it("are not asked again for a sample the backend does not answer for until the viewer holds it anew", () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message), after);
    const held = {};

    fields.ask(frame(1, held), ["id"]);
    fields.receive(answer(sent, 1, null));
    expect(fields.view(frame(1, held), ["id"]).state).toBe("unavailable");
    fields.ask(frame(1, held), ["id"]);
    expect(sent).toHaveLength(1);
    fields.ask(frame(1, {}), ["id"]);
    expect(sent).toHaveLength(2);
  });

  it("are asked afresh when a list reopens, ignoring a held refusal from its previous request", async () => {
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message), after);
    const held = frame(1);

    const close = fields.subscribe(() => {});
    fields.ask(held, ["id"]);
    fields.ask(held, ["id"]);
    expect(sent).toHaveLength(1);
    let release!: () => void;
    const heldReply = answer(sent, 1, null);
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    const late = waiting.then(() => fields.receive(heldReply));
    close();

    // The list opens again on the same sample, and nothing else asks.
    fields.subscribe(() => {});
    fields.ask(held, ["id"]);
    expect(sent).toHaveLength(2);
    release();
    await late;
    expect(fields.view(held, ["id"]).state).toBe("pending");
    fields.receive(answer(sent, 1, []));
    expect(fields.view(held, ["id"]).state).toBe("ready");
  });

  it("are sent again while a list listens and the answer is dropped, a few times, and not once answered", () => {
    vi.useFakeTimers();
    const sent: ClientMessage[] = [];
    const fields = new FrameFields((message) => sent.push(message), after);
    const held = frame(1);

    // Mounted, one sample, nothing else asks: the gate dropped the answer.
    const close = fields.subscribe(() => {});
    fields.ask(held, ["id"]);
    vi.advanceTimersByTime(2000);
    expect(sent).toHaveLength(2);
    vi.advanceTimersByTime(60_000);
    expect(sent).toHaveLength(4);

    fields.ask(frame(2), ["id"]);
    fields.receive(answer(sent, 2, []));
    vi.advanceTimersByTime(60_000);
    expect(sent).toHaveLength(5);

    // Wanting a sample already answered drops the request out for another, which is not sent again.
    fields.ask(frame(3), ["id"]);
    fields.ask(frame(2), ["id"]);
    vi.advanceTimersByTime(60_000);
    expect(sent).toHaveLength(6);

    // A list that stops listening stops the asking.
    fields.ask(frame(4), ["id"]);
    close();
    vi.advanceTimersByTime(60_000);
    expect(sent).toHaveLength(7);
    vi.useRealTimers();
  });
});
