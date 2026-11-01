import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { PlaybackView } from "./view";
import { Transport } from "./Transport";

afterEach(cleanup);

const view = (change: Partial<PlaybackView> = {}) => ({
  status: "ready" as const,
  progress: 1,
  frameCount: 100,
  playing: false,
  speed: 1,
  playhead: 30,
  shown: { sequenceId: 29, timestampUs: 1_160_000 },
  lanes: [],
  catalogs: [],
  width: 64,
  height: 48,
  warnings: [],
  ...change,
});

function setup(change: Partial<PlaybackView> = {}) {
  const controls = { togglePlay: vi.fn(), step: vi.fn(), seek: vi.fn(), setSpeed: vi.fn() };
  const result = render(<Transport controls={controls} view={view(change)} />);
  return { controls, ...result };
}

describe("transport", () => {
  it("plays, pauses and steps, naming the button after what it does now", () => {
    const { controls, rerender } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    fireEvent.click(screen.getByRole("button", { name: "Back 1 frame" }));
    fireEvent.click(screen.getByRole("button", { name: "Forward 1 frame" }));
    expect(controls.togglePlay).toHaveBeenCalledTimes(1);
    expect(controls.step.mock.calls).toEqual([[-1], [1]]);
    rerender(<Transport controls={controls} view={view({ playing: true })} />);
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  });

  it("scrubs: the seek bar shows the position asked for and every move seeks", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
    expect(bar.value).toBe("30");
    expect(bar.max).toBe("99");
    fireEvent.change(bar, { target: { value: "64" } });
    fireEvent.change(bar, { target: { value: "65" } });
    expect(controls.seek.mock.calls).toEqual([[64], [65]]);
  });

  it("reads out the frame on screen, not the one asked for", () => {
    setup();
    expect(screen.getByLabelText("Frame").textContent).toBe("29 / 99");
    expect(screen.getByLabelText("Time").textContent).toBe("0:01.16");
  });

  it("shows no frame or time before the first frame arrives", () => {
    setup({ shown: undefined });
    expect(screen.getByLabelText("Frame").textContent).toBe("… / 99");
    expect(screen.getByLabelText("Time").textContent).toBe("…");
  });

  it("sets the speed as a number is typed, and ignores a field that is not one yet", () => {
    const { controls } = setup();
    const field = screen.getByRole("spinbutton", { name: "Speed" });
    fireEvent.change(field, { target: { value: "2.5" } });
    fireEvent.change(field, { target: { value: "" } });
    expect(controls.setSpeed.mock.calls).toEqual([[2.5]]);
  });

  it("says why the frame the screen waits on cannot be shown", () => {
    setup({ frameError: { sequenceId: 30, reason: "corrupt slice" } });
    expect(screen.getByRole("alert").textContent).toBe("Frame 30 cannot be shown: corrupt slice");
  });
});
