// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { JpegFrame, ServerMessage, VideoFrame } from "./protocol";
import { Inbox, type Incoming, type Received } from "./wire";

const jpeg = (sequenceId: number): Received => ({
  type: "jpeg",
  frame: { sequenceId } as JpegFrame,
});
const live: Received = { type: "message", message: { type: "LiveOpened" } as ServerMessage };

/** An inbox whose decodes finish only when the test says so, and what it handed on, as frame numbers and types. */
function inbox() {
  const decoding: Array<() => void> = [];
  const delivered: Array<number | string> = [];
  const box = new Inbox(
    (received) =>
      received.type === "jpeg"
        ? new Promise<Incoming>((done) =>
            decoding.push(() =>
              done({
                type: "envelope",
                envelope: {
                  kind: "frame",
                  frame: { sequenceId: received.frame.sequenceId } as VideoFrame,
                },
              }),
            ),
          )
        : Promise.resolve(received as Incoming),
    (incoming) =>
      delivered.push(
        incoming.type === "envelope" && incoming.envelope.kind === "frame"
          ? incoming.envelope.frame.sequenceId
          : incoming.type,
      ),
  );
  const settle = async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  };
  const finish = async () => {
    decoding.shift()?.();
    await settle();
  };
  return { box, decoding, delivered, finish, settle };
}

describe("Inbox", () => {
  it("hands an offline stream's frames on in order, every one, decoding one at a time", async () => {
    const { box, decoding, delivered, finish } = inbox();
    box.push(jpeg(1));
    box.push({ type: "malformed", reason: "x" });
    box.push(jpeg(2));
    box.push(jpeg(3));
    expect(decoding).toHaveLength(1);
    for (let i = 0; i < 3; i++) await finish();
    expect(delivered).toEqual([1, "malformed", 2, 3]);
  });

  it("keeps only a live stream's newest waiting frame while a decode is slow", async () => {
    const { box, delivered, finish, settle } = inbox();
    box.push(live);
    await settle();
    for (let id = 1; id <= 200; id++) box.push(jpeg(id));
    await finish();
    await finish();
    await finish();
    expect(delivered).toEqual(["message", 1, 200]);
  });

  it("drops the offline frames outside the newest Want's window, as the viewer would", async () => {
    const { box, delivered, finish } = inbox();
    box.want({ playhead: 0, ahead: 5, behind: 0 });
    for (let id = 0; id <= 5; id++) box.push(jpeg(id));
    box.want({ playhead: 100, ahead: 2, behind: 1 });
    box.push(jpeg(4));
    for (let id = 99; id <= 102; id++) box.push(jpeg(id));
    for (let i = 0; i < 6; i++) await finish();
    expect(delivered).toEqual([0, 99, 100, 101, 102]);
  });

  it("drops the frames still waiting when the socket closes, and hands on the close", async () => {
    const { box, delivered, finish } = inbox();
    box.push(jpeg(1));
    box.push(jpeg(2));
    box.push({ type: "closed", code: 1006 });
    await finish();
    expect(delivered).toEqual([1, "closed"]);
  });
});
