import { expect, it, vi } from "vite-plus/test";
import { waitFor } from "@testing-library/react";
import { SELECTION, frame, CLASS, setup } from "./inspectionTestRig";
import { inspectorLines } from "./fields";
import type { InspectionAccess } from "../lanes/contract";
import type { Inspections } from "./inspection";
import type { ServerMessage } from "../stream/protocol";

const access = (owner: Inspections): InspectionAccess => {
  const view = owner.view;
  if (view.state !== "ready" || !view.access) throw Error("ready");
  return view.access;
};
const initial = {
  state: "Found" as const,
  fields: [{ ...CLASS, cut: [0, 20000] as [number, number] }],
  truncated: true,
  continuation: { token: "current-capability", next: 4096 },
};

it("drops a released first read when the Inspector reappears on the same paused sample, and drops a cancelled first Raw chunk", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  try {
    const { inspections: owner, reply, sent } = setup();
    owner.follow([frame(0)]);
    owner.select(SELECTION);
    const earlier = reply(0, { state: "Unavailable" });
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const late = held.then(() => owner.receive(earlier));
    owner.setVisible(false);
    expect(sent.at(-1)).toEqual({ type: "InspectRelease", request: earlier.request });
    owner.setVisible(true);
    expect(sent.at(-1)).toMatchObject({ type: "Inspect", request: expect.any(Number) });
    release();
    expect(await late).toBe(false);
    expect(sent.filter((message) => message.type === "InspectRelease")).toHaveLength(1);
    owner.receive(reply(0, initial));
    expect(owner.view.state).toBe("ready");
    access(owner).raw();
    const raw = sent.at(-1);
    if (raw?.type !== "InspectContinue") throw Error("Raw request");
    access(owner).cancel();
    access(owner).raw();
    const current = sent.at(-1);
    if (current?.type !== "InspectContinue") throw Error("Raw request");
    const chunk = { state: "Chunk" as const, text: "{}", total: 2, next: null };
    expect(
      owner.receiveContinuation({ type: "InspectContinued", request: raw.request, result: chunk }),
    ).toBe(false);
    expect(writeText).not.toHaveBeenCalled();
    owner.receiveContinuation({
      type: "InspectContinued",
      request: current.request,
      result: chunk,
    });
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith("{}"));
    owner.setVisible(false);
    expect(sent.at(-1)).toEqual({ type: "InspectRelease", token: initial.continuation.token });
  } finally {
    vi.unstubAllGlobals();
  }
});

it("restarts an interrupted pin scan at zero after copying, and makes refusal terminal for that attempt", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  try {
    const { inspections: owner, reply, sent } = setup();
    owner.follow([frame(0)]);
    owner.select(SELECTION);
    owner.receive(reply(0, initial));
    const pins = [{ group: "debug" as const, path: ["late"], identity: "late-digest" }];
    const lastRead = () => {
      const message = sent.at(-1);
      if (message?.type !== "InspectContinue") throw Error("read");
      return message;
    };
    const answer = (result: Extract<ServerMessage, { type: "InspectContinued" }>["result"]) =>
      owner.receiveContinuation({ type: "InspectContinued", request: lastRead().request, result });
    owner.setPins(pins);
    answer({ state: "Pins", fields: [], next: 4096 });
    const interrupted = lastRead();
    expect(interrupted.offset).toBe(4096);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const late = held.then(() =>
      owner.receiveContinuation({
        type: "InspectContinued",
        request: interrupted.request,
        result: {
          state: "Pins",
          fields: [
            [
              0,
              {
                group: "debug",
                path: ["late"],
                identity: "late-digest",
                value: { kind: "text", value: "discarded" },
              },
            ],
          ],
          next: null,
        },
      }),
    );
    access(owner).copy(0);
    release();
    expect(await late).toBe(false);
    answer({ state: "Chunk", text: "x".repeat(20000), total: 20000, next: null });
    await waitFor(() => expect(lastRead()).toMatchObject({ pins: expect.any(Array), offset: 0 }));
    const pinReads = () =>
      sent.filter((message) => message.type === "InspectContinue" && message.pins);
    expect(pinReads()).toHaveLength(3);
    const refused = lastRead();
    answer({ state: "Refused", reason: "Pinned inspection expired; select the object again" });
    for (let i = 0; i < 3; i++)
      expect(
        owner.receiveContinuation({
          type: "InspectContinued",
          request: refused.request,
          result: { state: "Refused", reason: "expired" },
        }),
      ).toBe(false);
    expect(pinReads()).toHaveLength(3);
    const view = owner.view;
    if (view.state !== "ready") throw Error("ready");
    expect(inspectorLines(view.fields, pins, new Set(), "7", view.pins).pinned[0]).toMatchObject({
      kind: "unavailable",
      pending: false,
      failed: true,
    });
    expect(writeText).toHaveBeenCalledExactlyOnceWith("x".repeat(20000));
  } finally {
    vi.unstubAllGlobals();
  }
});

it("settles unanswered first reads, pages and pin scans without a request loop", () => {
  vi.useFakeTimers();
  const after = (run: () => void, ms: number) => {
    const timer = setTimeout(run, ms);
    return () => clearTimeout(timer);
  };
  try {
    const { inspections: owner, sent, reply, time } = setup(after);
    owner.follow([frame(0)]);
    owner.select(SELECTION);
    // A retained overlay on a 250-Hz video must receive the same complete response window as a paused one.
    for (let elapsed = 0; elapsed < 7999; elapsed += 4) {
      const step = Math.min(4, 7999 - elapsed);
      time.now += step;
      vi.advanceTimersByTime(step);
      owner.follow([frame(0)], true);
    }
    expect(owner.view.state).toBe("loading");
    expect(sent.filter((m) => m.type === "InspectRelease")).toHaveLength(0);
    vi.advanceTimersByTime(1);
    const attempts = sent.filter((m) => m.type === "Inspect");
    expect(attempts).toHaveLength(4);
    expect(new Set(attempts.map((attempt) => attempt.request)).size).toBe(1);
    expect(owner.view.state).toBe("unavailable");
    vi.advanceTimersByTime(60_000);
    expect(sent.filter((m) => m.type === "Inspect")).toHaveLength(4);
    owner.setVisible(false);
    owner.setVisible(true);
    vi.advanceTimersByTime(7999);
    owner.receive(reply(0, initial));
    expect(owner.view.state).toBe("ready");
    access(owner).next?.();
    vi.advanceTimersByTime(8000);
    expect(access(owner).error).toBe("Details are not available");
    expect(access(owner).next).not.toBeNull();
    owner.setPins([{ group: "debug", path: ["late"], identity: "late-digest" }]);
    vi.advanceTimersByTime(8000);
    const count = sent.length;
    const view = owner.view;
    if (view.state !== "ready") throw Error("ready");
    expect(view.pins).toEqual(["unavailable"]);
    vi.advanceTimersByTime(60_000);
    expect(sent).toHaveLength(count);
    owner.reconnect();
    owner.follow([frame(0)]);
    owner.receive(reply(0, initial));
    expect(owner.view.state).toBe("ready");
    owner.disconnect();
  } finally {
    vi.useRealTimers();
  }
}, 5000);

it("settles a stalled or over-budget Raw copy before any clipboard write", () => {
  vi.useFakeTimers();
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const after = (run: () => void, ms: number) => {
    const timer = setTimeout(run, ms);
    return () => clearTimeout(timer);
  };
  try {
    const { inspections: owner, sent, reply } = setup(after);
    owner.follow([frame(0)]);
    owner.select(SELECTION);
    owner.receive(reply(0, initial));
    access(owner).raw();
    vi.advanceTimersByTime(8000);
    expect(access(owner).progress).toBeNull();
    expect(access(owner).error).toBe("Details are not available");
    for (const result of [
      { state: "Chunk" as const, text: "x", total: 268435457, next: 1 },
      { state: "Chunk" as const, text: "界".repeat(21846), total: 100000, next: 65538 },
      { state: "Chunk" as const, text: "", total: 1, next: 0 },
    ]) {
      access(owner).raw();
      const request = sent.at(-1);
      if (request?.type !== "InspectContinue") throw Error("Raw request");
      owner.receiveContinuation({ type: "InspectContinued", request: request.request, result });
      expect(access(owner).progress).toBeNull();
      expect(access(owner).error).toBe("Details are not available");
    }
    expect(writeText).not.toHaveBeenCalled();
    owner.disconnect();
  } finally {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  }
}, 5000);
