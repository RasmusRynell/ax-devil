import { expect, it, vi } from "vite-plus/test";
import { waitFor } from "@testing-library/react";
import { fullSample } from "../present/fixtures";
import type { LaneFrame } from "./contract";
import { Inspections } from "./inspection";
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
            group: "debug",
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
    access().copy(0);
    const request = sent.at(-1);
    if (request?.type !== "InspectContinue") throw Error("copy request");
    owner.receiveContinuation({
      type: "InspectContinued",
      request: request.request,
      result: { state: "Chunk", text: "x".repeat(20000), total: 20000, next: null },
    });
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
