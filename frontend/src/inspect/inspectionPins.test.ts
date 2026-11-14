import { expect, it, vi } from "vite-plus/test";
import { fullSample } from "../present/fixtures";
import { inspectorLines } from "./fields";
import type { LaneFrame } from "../lanes/contract";
import { Inspections } from "./inspection";
import type { ClientMessage, FieldWire, ServerMessage } from "../stream/protocol";

it("resolves a page-two pin on page one and afresh on each sample, including whole-value copy", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  try {
    const sent: ClientMessage[] = [];
    const owner = new Inspections((message) => sent.push(message));
    const frame = (sample: number) =>
      ({
        source: 0,
        dataRevision: 1,
        sample,
        columns: fullSample([{ id: "7", class: "car" }]),
        styleRevision: 0,
        classMask: { enabled: new Uint32Array(1), options: 0 },
        filter: null,
      }) as LaneFrame;
    const first: FieldWire = { group: "geometry", path: ["x"], value: { kind: "float", value: 0 } };
    const late = (text: string): FieldWire => ({
      group: "debug",
      path: ["late"],
      identity: "digest-of-full-path",
      value: { kind: "text", value: text },
      cut: [5000, 20000],
    });
    const initial = (sample: number) =>
      owner.receive({
        type: "Inspected",
        request: sent.filter((message) => message.type === "Inspect").at(-1)?.request ?? -1,
        source: 0,
        data_revision: 1,
        sample,
        entity: "7",
        inspection: {
          state: "Found",
          fields: [first],
          truncated: true,
          continuation: { token: `sample-${sample}`, next: 4096 },
        },
      });
    const access = () => {
      const view = owner.view;
      if (view.state !== "ready" || !view.access) throw Error("ready");
      return view.access;
    };
    const answer = (result: Extract<ServerMessage, { type: "InspectContinued" }>["result"]) => {
      const request = sent.at(-1);
      if (request?.type !== "InspectContinue") throw Error("request");
      owner.receiveContinuation({ type: "InspectContinued", request: request.request, result });
    };
    owner.follow([frame(0)]);
    owner.select({ lane: 0, source: 0, entity: "7" });
    initial(0);
    access().next?.();
    answer({ state: "Page", fields: [late("old preview")], next: null });
    const pins = [{ group: "debug" as const, path: ["late"], identity: "digest-of-full-path" }];
    owner.setPins(pins);
    expect(sent.at(-1)).toMatchObject({
      pins: [{ group: "debug", identity: "digest-of-full-path", path: [] }],
      offset: 0,
    });
    answer({ state: "Pins", fields: [], next: 4096 });
    answer({ state: "Pins", fields: [[0, late("old preview")]], next: null });
    access().first?.();
    answer({ state: "Page", fields: [first], next: 4096 });
    const pinned = () => {
      const view = owner.view;
      if (view.state !== "ready") throw Error("ready");
      return inspectorLines(view.fields, pins, new Set(), "7", view.pins).pinned[0];
    };
    expect(pinned()).toMatchObject({ kind: "field", value: { value: "old preview" } });
    owner.follow([frame(1)]);
    initial(1);
    expect(pinned()).toMatchObject({ kind: "unavailable", pending: true });
    answer({ state: "Pins", fields: [[0, late("new preview")]], next: null });
    expect(pinned()).toMatchObject({ kind: "field", value: { value: "new preview" } });
    access().copy(5000);
    expect(sent.at(-1)).toMatchObject({ field: 5000, token: "sample-1" });
    answer({ state: "Chunk", text: "n".repeat(20000), total: 20000, next: null });
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledExactlyOnceWith("n".repeat(20000));
    owner.follow([frame(2)]);
    initial(2);
    answer({ state: "Pins", fields: [], next: null });
    expect(pinned()).toMatchObject({ kind: "unavailable", pending: false });
  } finally {
    vi.unstubAllGlobals();
  }
});
