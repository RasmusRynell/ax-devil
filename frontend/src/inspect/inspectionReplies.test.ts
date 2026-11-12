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
