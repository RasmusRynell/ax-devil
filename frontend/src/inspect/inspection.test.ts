// @vitest-environment node
import { describe, expect, it, vi } from "vite-plus/test";
import { fullSample } from "../present/fixtures";
import { compileFilter } from "../present";
import { SELECTION, withCar, withoutCar, frame, CLASS, found, setup } from "./inspectionTestRig";
import { Inspections } from "./inspection";

describe("inspections", () => {
  it("follows a playing video's draws at most ten times a second, and catches up when asked or on an action", () => {
    const { inspections, samples, time } = setup();
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    time.now = 1000;
    inspections.follow([frame(2)], true);
    time.now = 1050;
    inspections.follow([frame(3)], true);
    expect(samples()).toEqual([1, 2]);

    inspections.catchUp();
    expect(samples()).toEqual([1, 2, 3]);

    time.now = 1100;
    inspections.follow([frame(4)], true);
    expect(samples()).toEqual([1, 2, 3]);
    time.now = 1150;
    inspections.follow([frame(5)], true);
    expect(samples()).toEqual([1, 2, 3, 5]);

    inspections.setVisible(false);
    time.now = 1160;
    inspections.follow([frame(6)], true);
    inspections.setVisible(true);
    expect(samples().at(-1)).toBe(6);
  });

  it("does not let a reply advance the view to a draw skipped while playing", () => {
    const { inspections, samples, time, reply } = setup();
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    time.now = 1000;
    inspections.follow([frame(2)], true);
    time.now = 1033;
    inspections.follow([frame(3)], true);
    inspections.receive(reply(2, found(CLASS)));
    expect(inspections.view).toMatchObject({ state: "ready" });
    time.now = 1066;
    inspections.follow([frame(4)], true);
    inspections.receive(reply(1, found(CLASS)));
    expect(samples()).toEqual([1, 2]);
    time.now = 1100;
    inspections.follow([frame(5)], true);
    expect(samples()).toEqual([1, 2, 5]);
  });

  it("stops waiting to catch up when hidden or suspended, and catches up when shown", () => {
    const { inspections, samples, time } = setup();
    const waiting: (() => void)[] = [];
    const after = (callback: () => void) => {
      waiting.push(callback);
      return () => void waiting.splice(waiting.indexOf(callback) >>> 0, 1);
    };
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    time.now = 1000;
    inspections.follow([frame(2)], true);
    time.now = 1010;
    inspections.follow([frame(3)], true);
    inspections.trail(after, () => {});
    expect(waiting).toHaveLength(1);
    inspections.setVisible(false);
    expect(waiting).toHaveLength(0);
    inspections.setVisible(true);
    expect(samples().at(-1)).toBe(3);

    time.now = 1020;
    inspections.follow([frame(4)], true);
    inspections.trail(after, () => {});
    inspections.suspend();
    expect(waiting).toHaveLength(0);
  });

  it("shows the summary of the shown sample at once and the fields when the reply comes", () => {
    const { inspections, sent, reply } = setup();
    inspections.follow([frame(3)]);
    expect(inspections.select(SELECTION)).toBe(true);
    expect(sent).toEqual([
      {
        type: "Inspect",
        request: expect.any(Number),
        source: 0,
        data_revision: 1,
        sample: 3,
        entity: "7",
      },
    ]);
    expect(inspections.view).toMatchObject({
      state: "loading",
      selection: SELECTION,
      summary: {
        entity: "7",
        className: "car",
        score: 0.82,
        confidence: 0.9,
        motion: "moving",
      },
      fields: [],
    });
    const loading = inspections.view;
    expect(inspections.follow([frame(3)])).toBe(false);
    expect(inspections.view).toBe(loading);

    expect(inspections.receive(reply(3, found(CLASS)))).toBe(true);
    expect(inspections.view).toMatchObject({ state: "ready", fields: [CLASS] });
    expect(sent).toHaveLength(1);
  });

  it("asks for each newly shown sample at once, so a reply that never comes holds nothing up", () => {
    const { inspections, samples, reply } = setup();
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    inspections.follow([frame(2)]);
    inspections.follow([frame(3)]);
    expect(samples()).toEqual([1, 2, 3]);
    expect(inspections.receive(reply(1, found(CLASS)))).toBe(false);
    expect(inspections.view.state).toBe("loading");
    inspections.receive(reply(3, found({ ...CLASS, value: { kind: "text", value: "bus" } })));
    expect(inspections.view).toMatchObject({
      state: "ready",
      fields: [{ value: { kind: "text", value: "bus" } }],
    });
    inspections.follow([frame(1)]);
    expect(inspections.view.state).toBe("loading");
    expect(samples()).toEqual([1, 2, 3, 1]);
  });

  it("never shows another sample's summary or fields while the shown sample's reply is out", () => {
    const { inspections, reply } = setup();
    const faster = fullSample([{ id: "7", class: "car", score: 0.5 }]);
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    inspections.receive(reply(1, found(CLASS)));
    inspections.follow([frame(2, faster)]);
    expect(inspections.view).toMatchObject({
      state: "loading",
      summary: { score: 0.5 },
      fields: [],
    });
    expect(
      inspections.receive(reply(1, found({ ...CLASS, value: { kind: "text", value: "bus" } }))),
    ).toBe(false);
    inspections.receive(reply(2, found({ ...CLASS, value: { kind: "text", value: "truck" } })));
    expect(inspections.view).toMatchObject({
      state: "ready",
      summary: { score: 0.5 },
      fields: [{ value: { kind: "text", value: "truck" } }],
    });
    inspections.follow([frame(3, withoutCar)]);
    expect(inspections.view.state).toBe("absent");
    inspections.follow([frame(4)]);
    expect(inspections.view).toMatchObject({
      state: "loading",
      summary: { score: 0.82 },
      fields: [],
    });
    inspections.receive(reply(4, found(CLASS)));
    inspections.select({ ...SELECTION, entity: "9" });
    inspections.follow([frame(5)]);
    expect(inspections.view).toMatchObject({ state: "loading", fields: [] });
  });

  it("keeps the current field window and asks again after leaving it", () => {
    const { inspections, samples, reply } = setup();
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
    inspections.follow([frame(0)]);
    expect(samples()).toHaveLength(18);
    expect(inspections.view.state).toBe("loading");
  });

  it("ignores a reply of a data revision the source has left", () => {
    const { inspections, sent, reply } = setup();
    const revisions = () => sent.flatMap((m) => (m.type === "Inspect" ? [m.data_revision] : []));
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.follow([frame(0, withCar, 2)], true);
    expect(inspections.receive(reply(0, found(CLASS), 1))).toBe(false);
    expect(revisions()).toEqual([1]);
    inspections.catchUp();
    expect(revisions()).toEqual([1, 2]);
    expect(inspections.view.state).toBe("loading");
    inspections.receive(reply(0, found(CLASS), 2));
    expect(inspections.view.state).toBe("ready");
  });

  it("keeps the selection when a frame has no such object, and finds the object again", () => {
    const { inspections, samples, sent, reply } = setup();
    inspections.follow([frame(0, withoutCar)]);
    inspections.select(SELECTION);
    expect(inspections.view).toEqual({
      state: "absent",
      selection: SELECTION,
      reason: "gone",
      last: null,
    });
    expect(samples()).toEqual([]);
    inspections.follow([frame(1)]);
    expect(inspections.view).toMatchObject({ state: "loading", selection: SELECTION });
    inspections.receive(reply(1, { state: "Absent" }));
    expect(sent.at(-1)).toEqual({
      type: "InspectRelease",
      request: sent.filter((message) => message.type === "Inspect").at(-1)?.request,
    });
    expect(inspections.view).toMatchObject({ state: "absent", reason: "gone" });
    inspections.follow([frame(2)]);
    inspections.receive(reply(2, found(CLASS)));
    const count = sent.length;
    inspections.follow([frame(2)], false);
    expect(sent).toHaveLength(count);
    inspections.follow([undefined]);
    expect(sent.at(-1)).toEqual({
      type: "InspectRelease",
      request: sent.filter((message) => message.type === "Inspect").at(-1)?.request,
    });
    inspections.follow([frame(3)]);
    expect(inspections.view.state).toBe("loading");
    inspections.receive(reply(3, found(CLASS)));
    inspections.follow([frame(4, withoutCar)]);
    expect(sent.at(-1)).toEqual({
      type: "InspectRelease",
      request: sent.filter((message) => message.type === "Inspect").at(-1)?.request,
    });
    expect(inspections.view).toMatchObject({ state: "absent", last: { className: "car" } });
  });

  it("says filtered, and keeps the selection and the class, while the lane's toggles or filter hide the object", () => {
    const { inspections, samples, sent, reply } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(reply(0, found(CLASS)));
    const off = { classMask: { enabled: new Uint32Array([0b10]), options: 2 }, styleRevision: 1 };
    expect(inspections.follow([frame(0, withCar, 1, 0, off)])).toBe(true);
    expect(sent.at(-1)).toEqual({
      type: "InspectRelease",
      request: sent.filter((message) => message.type === "Inspect").at(-1)?.request,
    });
    expect(inspections.view).toMatchObject({
      state: "absent",
      selection: SELECTION,
      reason: "filtered",
      last: { className: "car" },
    });
    expect(inspections.follow([frame(0, withCar, 1, 0, { styleRevision: 2 })])).toBe(true);
    expect(inspections.view.state).toBe("loading");

    const compiled = compileFilter("score > 0.9");
    if (!compiled.ok) throw new Error("the filter compiles");
    inspections.follow([frame(0, withCar, 1, 0, { filter: compiled.filter, styleRevision: 3 })]);
    expect(inspections.view).toMatchObject({ state: "absent", reason: "filtered" });
    expect(samples()).toEqual([0, 0]);
  });

  it("says unavailable for a sample the backend will not answer for, and asks again only when it is shown anew", () => {
    const { inspections, samples, reply } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(reply(0, { state: "Unavailable" }));
    expect(inspections.view).toMatchObject({ state: "unavailable", summary: { className: "car" } });
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0]);
    inspections.follow([frame(1)]);
    inspections.follow([frame(0)]);
    expect(samples()).toEqual([0, 1, 0]);
    inspections.receive(reply(0, { state: "Unavailable" }));
    expect(inspections.follow([frame(0, fullSample([{ id: "7", class: "car" }]))])).toBe(true);
    expect(samples()).toEqual([0, 1, 0, 0]);
    expect(inspections.view.state).toBe("loading");
  });

  it("keeps nothing of another source's object that has the same id", () => {
    const { inspections, sent, reply } = setup();
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
    expect(asked).toMatchObject({
      type: "Inspect",
      request: expect.any(Number),
      source: 1,
      entity: "7",
    });
    inspections.receive(reply(0, found({ ...CLASS, value: { kind: "text", value: "bus" } })));
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
    const { inspections, samples, reply } = setup();
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

  it("asks for the selection's fields only while an inspector page shows, and at once when one does", () => {
    const { inspections, samples } = setup();
    inspections.follow([frame(0)]);
    inspections.setVisible(false);
    inspections.select(SELECTION);
    inspections.follow([frame(1)]);
    inspections.follow([frame(2)]);
    expect(samples()).toEqual([]);
    expect(inspections.view).toMatchObject({ state: "loading", selection: SELECTION });
    inspections.setVisible(true);
    expect(samples()).toEqual([2]);
  });

  it("clears the selection", () => {
    const { inspections, reply } = setup();
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(reply(0, found(CLASS)));
    expect(inspections.select(null)).toBe(true);
    expect(inspections.view).toEqual({ state: "none" });
    expect(inspections.select(null)).toBe(false);
    inspections.follow([frame(1)]);
    inspections.select(SELECTION);
    expect(inspections.view).toMatchObject({ state: "loading", fields: [] });
  });
});

it("replaces field pages and copies complete Unicode text while a superseded reply waits for release", async () => {
  const { inspections, sent, reply } = setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  try {
    const access = () => {
      const view = inspections.view;
      if (view.state === "none" || view.state === "absent" || !view.access)
        throw Error("ready inspection");
      return view.access;
    };
    const request = () => {
      const message = sent.at(-1);
      if (message?.type !== "InspectContinue") throw Error("continuation");
      return message.request;
    };
    inspections.follow([frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(
      reply(0, {
        state: "Found",
        fields: [CLASS],
        truncated: true,
        continuation: { token: "socket-capability", next: 4096 },
      }),
    );
    access().next?.();
    const text = `${"a".repeat(65535)}😀é`;
    inspections.receiveContinuation({
      type: "InspectContinued",
      request: request(),
      result: {
        state: "Page",
        fields: [
          {
            group: "debug",
            path: ["last"],
            value: { kind: "text", value: "preview" },
            cut: [4096, 65541],
          },
        ],
        next: null,
      },
    });
    expect(inspections.view).toMatchObject({ fields: [{ path: ["last"] }] });
    access().copy(4096);
    inspections.receiveContinuation({
      type: "InspectContinued",
      request: request(),
      result: { state: "Chunk", text: "a".repeat(65535), total: 65541, next: 65535 },
    });
    expect(writeText).not.toHaveBeenCalled();
    expect(access().progress).toEqual({ received: 65535, total: 65541 });
    inspections.receiveContinuation({
      type: "InspectContinued",
      request: request(),
      result: { state: "Chunk", text: "😀é", total: 65541, next: null },
    });
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(text);
    writeText.mockClear();
    access().copy(4096);
    const earlier = request();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const late = held.then(() =>
      inspections.receiveContinuation({
        type: "InspectContinued",
        request: earlier,
        result: { state: "Chunk", text: "late", total: 4, next: null },
      }),
    );
    inspections.follow([frame(1)]);
    release();
    expect(await late).toBe(false);
    expect(writeText).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
  }
});

it("cancels a copy on hide, lane change, explicit cancellation and reconnect", () => {
  for (const cancel of [
    (owner: Inspections) => owner.setVisible(false),
    (owner: Inspections) => owner.select({ ...SELECTION, lane: 1 }),
    (owner: Inspections) => owner.reconnect(),
    (owner: Inspections) => {
      const view = owner.view;
      if (view.state === "ready") view.access?.cancel();
    },
  ]) {
    const { inspections, sent, reply } = setup();
    inspections.follow([frame(0), frame(0)]);
    inspections.select(SELECTION);
    inspections.receive(
      reply(0, {
        state: "Found",
        fields: [{ ...CLASS, cut: [0, 20000] }],
        truncated: false,
        continuation: { token: "socket", next: null },
      }),
    );
    const view = inspections.view;
    if (view.state !== "ready") throw Error("ready");
    view.access?.copy(0);
    const message = sent.at(-1);
    if (message?.type !== "InspectContinue") throw Error("copy request");
    cancel(inspections);
    expect(
      inspections.receiveContinuation({
        type: "InspectContinued",
        request: message.request,
        result: { state: "Chunk", text: "cancelled", total: 20000, next: null },
      }),
    ).toBe(false);
  }
});
