import { expect, it, vi } from "vite-plus/test";
import { act, waitFor } from "@testing-library/react";
import { fullSample } from "../present/fixtures";
import type { LaneFrame } from "./contract";
import { Inspections } from "./inspection";
import { LaneSet } from "./lanes";
import { ViewerData } from "./viewerData";
import type { ClientMessage } from "./protocol";

it("discards a clipboard retry as soon as the sample changes, before the playing view catches up", async () => {
  const writeText = vi.fn().mockRejectedValue(new Error("Clipboard unavailable"));
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });
  try {
    const sent: ClientMessage[] = [];
    const owner = new Inspections(
      (message) => sent.push(message),
      () => 0,
    );
    startCopy(owner, sent);
    const access = () => {
      const view = owner.view;
      if (view.state !== "ready" || !view.access) throw Error("ready inspection");
      return view.access;
    };
    await waitFor(() => expect(access().commit).not.toBeNull());
    expect(writeText).toHaveBeenCalledTimes(1);
    owner.follow([frame(1)], true);
    access().commit?.();
    expect(writeText).toHaveBeenCalledTimes(1);
  } finally {
    Reflect.deleteProperty(document, "execCommand");
    vi.unstubAllGlobals();
  }
});

const frame = (sample: number): LaneFrame =>
  ({
    source: 0,
    dataRevision: 1,
    sample,
    columns: fullSample([{ id: "7", class: "car" }]),
    styleRevision: 0,
    classMask: { enabled: new Uint32Array(1), options: 0 },
    filter: null,
  }) as LaneFrame;

function startCopy(owner: Inspections, sent: ClientMessage[], wholeRaw?: string) {
  owner.follow([frame(0)]);
  owner.select({ lane: 0, source: 0, entity: "7" });
  owner.receive({
    type: "Inspected",
    source: 0,
    data_revision: 1,
    sample: 0,
    entity: "7",
    inspection: {
      state: "Found",
      fields: [
        {
          group: wholeRaw ? "raw" : "debug",
          path: ["text"],
          value: { kind: "text", value: "preview" },
          cut: [0, 20000],
        },
      ],
      truncated: false,
      continuation: { token: "held-sample", next: null },
    },
  });
  const access = () => {
    const view = owner.view;
    if (view.state !== "ready" || !view.access) throw Error("ready inspection");
    return view.access;
  };
  if (wholeRaw) access().raw();
  else access().copy(0);
  const request = sent.at(-1);
  if (request?.type !== "InspectContinue") throw Error("copy request");
  owner.receiveContinuation({
    type: "InspectContinued",
    request: request.request,
    result: {
      state: "Chunk",
      text: wholeRaw ?? "x".repeat(20000),
      total: wholeRaw?.length ?? 20000,
      next: null,
    },
  });
}

it("closing the real viewer invalidates a pending clipboard write before its refusal can fall back", async () => {
  let release!: () => void;
  const pending = new Promise<void>((_, reject) => {
    release = () => reject(Error("denied"));
  });
  const writeText = vi.fn(() => pending);
  const fallback = vi.fn(() => true);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  Object.defineProperty(document, "execCommand", { value: fallback, configurable: true });
  try {
    const sent: ClientMessage[] = [];
    const data = new ViewerData(
      new LaneSet(),
      (message) => sent.push(message),
      () => () => {},
    );
    startCopy(data.inspections, sent);
    expect(writeText).toHaveBeenCalledTimes(1);
    const view = data.inspections.view;
    if (view.state !== "ready" || !view.access) throw Error("ready inspection");
    view.access.cancel();
    startCopy(data.inspections, sent);
    expect(writeText).toHaveBeenCalledTimes(2);
    data.close();
    expect(sent.at(-1)).toEqual({ type: "InspectRelease" });
    await act(async () => release());
    expect(fallback).not.toHaveBeenCalled();
    expect(data.inspections.view).toEqual({ state: "none" });
  } finally {
    Reflect.deleteProperty(document, "execCommand");
    vi.unstubAllGlobals();
  }
});

it("copies complete Raw JSON through the transfer even when only a preview from the group is in the window", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  try {
    const sent: ClientMessage[] = [];
    const owner = new Inspections((message) => sent.push(message));
    const complete = JSON.stringify({ end_reason: "x".repeat(20000), frame_number: 17 }, null, 2);
    startCopy(owner, sent, complete);
    expect(sent.at(-1)).toMatchObject({
      type: "InspectContinue",
      raw: true,
      field: null,
      offset: 0,
    });
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(complete);
  } finally {
    vi.unstubAllGlobals();
  }
});
