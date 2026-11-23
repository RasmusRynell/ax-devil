import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { NetworkRow } from "../generated/api";
import type { Camera, Stream } from "./cameras";
import { useLoginChecks } from "./checks";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const refused: NetworkRow = {
  address: "10.0.0.2",
  scheme: "https",
  port: 443,
  identity: { model: "Q1656", serial: "S1", firmware: "11.8.1", product_type: "Box Camera" },
  uptime_s: 0,
  sign_in: { state: "Refused" },
};

const camera = (stream: Partial<Stream>): Camera => ({
  key: "k",
  name: "Lobby",
  address: "10.0.0.2",
  hosts: ["10.0.0.2"],
  streams: [
    {
      id: "a",
      label: "Lobby",
      open: true,
      state: "ready",
      overlay: "none",
      lane: null,
      recipe: { host: "10.0.0.2" },
      info: [],
      reason: "",
      editable: true,
      generation: 1,
      ...stream,
    },
  ],
});

describe("login checks", () => {
  it("drop a refusal that arrives after the stream went pending, was removed or was stopped", async () => {
    const answers: ((row: NetworkRow) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      () =>
        new Promise((resolve) =>
          answers.push((row) => resolve({ status: 200, json: async () => row })),
        ),
    );
    const late = async (change: Camera[]) => {
      answers.length = 0;
      const { result, rerender } = renderHook(({ cameras }) => useLoginChecks(cameras, true, 0), {
        initialProps: { cameras: [camera({})] },
      });
      await waitFor(() => expect(answers.length).toBeGreaterThan(0));
      rerender({ cameras: change });
      answers[0]?.(refused);
      await new Promise((resolve) => setTimeout(resolve, 0));
      return result.current.has("a");
    };
    expect(await late([camera({ state: "resolving", recipe: null })])).toBe(false);
    expect(await late([])).toBe(false);
    expect(await late([camera({ open: false })])).toBe(false);
  });
});
