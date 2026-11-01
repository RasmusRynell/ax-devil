// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { TextAtlas } from "../overlay/atlas";
import { fakeGlyphs } from "../overlay/fixtures";
import { OverlayPreparer } from "../overlay/preparer";
import { boxesSection, overlayBytes } from "./fakeOverlay";
import { LANE_DEFAULTS } from "./fakeOpened";
import { Receiver, buffersOf, decode } from "./wire";

describe("what the socket delivers", () => {
  it("reads a control message", () => {
    expect(decode('{"type":"Indexing","progress":0.5}')).toEqual({
      type: "message",
      message: { type: "Indexing", progress: 0.5 },
    });
  });

  it("reports a message that breaks the protocol, so the session can end", () => {
    expect(decode("not json")?.type).toBe("malformed");
    expect(decode(new ArrayBuffer(40))?.type).toBe("malformed");
  });
});

const want = (playhead: number, ahead = 2, behind = 1) =>
  ({ type: "Want", playhead, ahead, behind, direction: "forward" }) as const;
const overlay = (sequenceId: number, lane = 0, revision = 0) =>
  overlayBytes({ lane, sequenceId, revision, sections: [boxesSection(1)] });

function receiver() {
  const preparer = new OverlayPreparer(new TextAtlas(fakeGlyphs()));
  return { receiver: new Receiver(preparer), preparer };
}

describe("receiving overlays in the worker", () => {
  it("prepares an overlay of the window with the density and target it came with", () => {
    const { receiver: input } = receiver();
    input.density = 2;
    input.sent(want(5));
    const incoming = input.receive(overlay(5, 1, 3));
    if (incoming?.type !== "overlays") throw new Error("expected prepared overlays");
    expect(incoming.overlays).toMatchObject([
      { lane: 1, sequenceId: 5, revision: 3, prepared: { dpr: 2, target: { width: 640 } } },
    ]);
    expect(buffersOf(incoming.overlays)).toHaveLength(3);
  });

  it("drops overlays outside the window the session last asked for, and what the window left behind", () => {
    const { receiver: input, preparer } = receiver();
    expect(input.receive(overlay(5))).toBeNull();
    input.sent(want(5));
    expect(input.receive(overlay(9))).toBeNull();
    expect(input.receive(overlay(4))?.type).toBe("overlays");
    expect(input.receive(overlay(7))?.type).toBe("overlays");
    expect(preparer.size).toBe(2);
    input.sent(want(8));
    expect(preparer.size).toBe(1);
  });

  it("prepares nothing for a hidden lane, and forgets what it held, until it is shown again", () => {
    const { receiver: input, preparer } = receiver();
    input.sent(want(5));
    input.receive(overlay(5));
    const view = (visible: boolean) =>
      ({ type: "LaneView", lane: 0, revision: 1, view: { ...LANE_DEFAULTS, visible } }) as const;
    input.sent(view(false));
    expect(preparer.size).toBe(0);
    expect(input.receive(overlay(5))).toBeNull();
    input.sent(view(true));
    expect(input.receive(overlay(5, 0, 1))?.type).toBe("overlays");
  });

  it("ends the session on instructions that break the format", () => {
    const { receiver: input } = receiver();
    input.sent(want(5));
    const odd = overlayBytes({ sequenceId: 5, sections: [[2, new Uint8Array(7)]] });
    expect(input.receive(odd)?.type).toBe("malformed");
  });
});
