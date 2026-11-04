import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { LiveResolutions } from "../generated/api";
import { askResolutions, useDiscovery } from "./discovery";

const sizes = (...widths: number[]): LiveResolutions => ({
  resolutions: widths.map((width) => ({ width, height: width / 2 })),
});

interface Call {
  url: string;
  body: { connection?: { host?: string } };
  answer: (status: number, json: unknown) => void;
}

/** A backend whose answers the test gives, one request at a time. */
function stubBackend() {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string, init: RequestInit) =>
        new Promise((resolve) =>
          calls.push({
            url,
            body: JSON.parse(init.body as string),
            answer: (status, json) => resolve({ ok: status < 400, status, json: async () => json }),
          }),
        ),
    ),
  );
  return calls;
}

const hostAsked = (host: string) =>
  renderHook(
    (props: { host: string }) =>
      useDiscovery((signal) => askResolutions({ host: props.host }, signal), props.host, 600),
    { initialProps: { host } },
  );

const settle = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("live discovery", () => {
  it("asks once the connection has stayed as typed for 600 ms, and drops an answer the form has moved past", async () => {
    const calls = stubBackend();
    const view = hostAsked("a");
    expect(view.result.current.state.phase).toBe("loading");

    await settle(400);
    view.rerender({ host: "b" });
    await settle(400);
    expect(calls).toHaveLength(0);
    await settle(200);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body.connection?.host).toBe("b");

    // The form moves on while that question is out; its answer comes last and must not show.
    view.rerender({ host: "c" });
    await settle(600);
    expect(calls).toHaveLength(2);
    calls[1]?.answer(200, sizes(1280));
    await settle(0);
    calls[0]?.answer(200, sizes(640));
    await settle(0);

    expect(view.result.current.state).toEqual({ phase: "ready", value: sizes(1280) });
  });

  it("asks whether to send a $NAME to its host, confirms it, and asks again", async () => {
    const calls = stubBackend();
    const view = hostAsked("10.0.0.7");
    await settle(600);
    const confirmation = { reference: "CAM_PASS", host: "10.0.0.7" };
    calls[0]?.answer(409, { problem: "NeedsConfirmation", confirmation });
    await settle(0);
    expect(view.result.current.state).toEqual({ phase: "confirm", confirmation });

    act(() => view.result.current.confirm());
    expect(calls[1]?.url).toBe("/api/live/confirm");
    expect(calls[1]?.body).toEqual(confirmation);
    calls[1]?.answer(204, null);
    await settle(0);
    await settle(0);
    expect(calls[2]?.url).toBe("/api/live/resolutions");
    calls[2]?.answer(200, sizes(1280));
    await settle(0);

    expect(view.result.current.state.phase).toBe("ready");
  });

  it("drops the answer to a confirmation the form has moved past", async () => {
    const calls = stubBackend();
    const view = hostAsked("a");
    await settle(600);
    calls[0]?.answer(409, {
      problem: "NeedsConfirmation",
      confirmation: { reference: "CAM_PASS", host: "a" },
    });
    await settle(0);
    act(() => view.result.current.confirm());

    view.rerender({ host: "b" });
    await settle(600);
    calls[2]?.answer(200, sizes(1280));
    await settle(0);
    expect(view.result.current.state).toEqual({ phase: "ready", value: sizes(1280) });

    calls[1]?.answer(500, null);
    await settle(0);
    expect(view.result.current.state).toEqual({ phase: "ready", value: sizes(1280) });
    expect(calls).toHaveLength(3);
  });
});
