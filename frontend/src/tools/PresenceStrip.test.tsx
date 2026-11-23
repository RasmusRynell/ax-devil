import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { PresenceStrip } from "./PresenceStrip";

afterEach(cleanup);

describe("presence strip", () => {
  it("asks for the frame under a click, kept within the video", () => {
    const onSeek = vi.fn();
    render(<PresenceStrip spans={[[10, 20]]} frames={100} category="car" onSeek={onSeek} />);
    const strip = screen.getByRole("img", { name: "Presence" });
    vi.spyOn(strip, "getBoundingClientRect").mockReturnValue({ left: 10, width: 200 } as DOMRect);
    for (const x of [110, 400, 0]) fireEvent.click(strip, { clientX: x });
    expect(onSeek.mock.calls).toEqual([[50], [99], [0]]);
  });
});
