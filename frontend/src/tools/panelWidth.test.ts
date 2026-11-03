import { describe, expect, it } from "vite-plus/test";
import { roomOf, settle } from "./panelWidth";

describe("media tools width", () => {
  // A 400 wide panel with a 300 wide video: the video may give up 140 pixels to it, no more.
  const room = roomOf(400, 300, 280);

  it("stays between what the content needs and what leaves the video 160 pixels", () => {
    expect(settle(350, room)).toEqual({ width: 350, close: false });
    expect(settle(900, room).width).toBe(540);
    expect(settle(200, room)).toEqual({ width: 280, close: false });
  });

  it("closes when dragged below half of what the content needs", () => {
    expect(settle(141, room).close).toBe(false);
    expect(settle(139, room)).toEqual({ width: 280, close: true });
  });

  it("puts the content before the video when the pane is too narrow for both", () => {
    expect(roomOf(280, 100, 280).most).toBe(280);
  });
});
