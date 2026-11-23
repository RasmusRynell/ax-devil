import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { renderHook, waitFor, act } from "@testing-library/react";
import type { NetworkRow, NetworkSnapshot } from "../generated/api";
import { compareScans, splitLines, useNetwork, type Order } from "./network";

const row = (address: string, firmware: string, extra: Partial<NetworkRow> = {}): NetworkRow => ({
  address,
  scheme: "http",
  port: 80,
  identity: { model: "Q1656", serial: "S", firmware, product_type: "Box Camera" },
  uptime_s: null,
  sign_in: { state: "NotTried" },
  ...extra,
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("order", () => {
  it("sorts firmware by its numbers with unknown last, ties by address, and a camera never scanned last", () => {
    const rows = [
      row("10.0.0.9", "11.10.1"),
      row("10.0.0.2", ""),
      row("10.0.0.10", "11.9.5"),
      row("10.0.0.3", "11.9.5"),
    ];
    const addresses = (order: Order) =>
      [...rows, null]
        .sort((a, b) => compareScans(order, a, b))
        .map((found) => found?.address ?? "never scanned");
    expect(addresses("firmware")).toEqual([
      "10.0.0.3",
      "10.0.0.10",
      "10.0.0.9",
      "10.0.0.2",
      "never scanned",
    ]);
    // Addresses compare by number, not by text.
    expect(addresses("address")).toEqual([
      "10.0.0.2",
      "10.0.0.3",
      "10.0.0.9",
      "10.0.0.10",
      "never scanned",
    ]);
  });
});

describe("snapshot stream", () => {
  it("joins lines split across chunks and keeps the unfinished one", () => {
    const first = splitLines("", '{"a":1}\n{"b"');
    expect(first).toEqual({ lines: ['{"a":1}'], rest: '{"b"' });
    expect(splitLines(first.rest, ":2}\n\n")).toEqual({ lines: ['{"b":2}'], rest: "" });
  });

  it("follows the stream and opens it again after a drop", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const snapshot = (scan: number): NetworkSnapshot => ({
      scan,
      scanning: false,
      rows: [row("10.0.0.2", "11.8.1")],
      failure: null,
    });
    const opened = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        opened();
        const text = `${JSON.stringify(snapshot(opened.mock.calls.length))}\n`;
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(text));
            controller.close();
          },
        });
        return new Response(body);
      }),
    );
    const { result, unmount } = renderHook(() => useNetwork(true));
    await waitFor(() => expect(result.current.snapshot?.scan).toBe(1));
    await waitFor(() => expect(result.current.dropped).toBe(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    await waitFor(() => expect(result.current.snapshot?.scan).toBe(2));
    unmount();
  });
});
