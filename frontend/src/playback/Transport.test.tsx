import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { INITIAL_VIEW, type PlaybackView } from "./view";
import { Transport } from "./Transport";

afterEach(cleanup);

const view = (change: Partial<PlaybackView> = {}): PlaybackView => ({
  ...INITIAL_VIEW,
  status: "ready",
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
  const controls = {
    togglePlay: vi.fn(),
    step: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    scrubStart: vi.fn(),
    scrubEnd: vi.fn(),
  };
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

  it("scrubs: the seek bar shows the position asked for, every move seeks, and holding it holds playback", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
    expect(bar.value).toBe("30");
    expect(bar.max).toBe("99");
    fireEvent.pointerDown(bar);
    fireEvent.change(bar, { target: { value: "64" } });
    fireEvent.change(bar, { target: { value: "65" } });
    expect(controls.seek.mock.calls).toEqual([[64], [65]]);
    expect(controls.scrubEnd).not.toHaveBeenCalled();
    fireEvent.pointerUp(window);
    expect(controls.scrubStart).toHaveBeenCalledTimes(1);
    expect(controls.scrubEnd).toHaveBeenCalledTimes(1);
  });

  it("ends a key hold on the seek bar when focus leaves it before the key is released", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" });
    fireEvent.keyDown(bar, { key: "PageUp" });
    expect(controls.scrubStart).toHaveBeenCalledTimes(1);
    fireEvent.blur(bar);
    expect(controls.scrubEnd).toHaveBeenCalledTimes(1);
  });

  it("has a seek bar of one position for a video without frames", () => {
    setup({ frameCount: 0 });
    const bar = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
    expect(bar.max).toBe("0");
    expect(screen.getByText("/ 0")).toBeTruthy();
  });

  it("reads out the frame on screen, not the one asked for", () => {
    setup();
    expect((screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement).value).toBe("29");
    expect(screen.getByText("/ 99")).toBeTruthy();
    expect(screen.getByLabelText("Time").textContent).toBe("0:01.16");
  });

  it("shows no frame or time before the first frame arrives", () => {
    setup({ shown: undefined });
    expect((screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement).value).toBe("");
    expect(screen.getByLabelText("Time").textContent).toBe("…");
  });

  it("goes to the frame typed, within the video, only when it is not where the seek bar is", () => {
    const { controls } = setup();
    const field = screen.getByRole("textbox", { name: "Frame" });
    const enter = (text: string) => {
      fireEvent.change(field, { target: { value: text } });
      fireEvent.keyDown(field, { key: "Enter" });
      fireEvent.blur(field);
    };
    enter("30");
    enter("500");
    enter("abc");
    fireEvent.change(field, { target: { value: "12" } });
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.blur(field);
    enter("7");
    expect(controls.seek.mock.calls).toEqual([[99], [7]]);
    expect((field as HTMLInputElement).value).toBe("29");
  });

  it("offers the preset speeds with the current one checked, and any other speed on the button alone", () => {
    const { controls, rerender } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Speed" }));
    expect(
      screen.getAllByRole("menuitemcheckbox").map((item) => [item.textContent, item.ariaChecked]),
    ).toEqual([
      ["0.25×", "false"],
      ["0.5×", "false"],
      ["1×", "true"],
      ["1.5×", "false"],
      ["2×", "false"],
      ["4×", "false"],
    ]);
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "1.5×" }));
    expect(controls.setSpeed.mock.calls).toEqual([[1.5]]);
    rerender(<Transport controls={controls} view={view({ speed: 1.3 })} />);
    const button = screen.getByRole("button", { name: "Speed" });
    expect(button.textContent).toBe("1.3×");
    fireEvent.click(button);
    expect(
      screen.getAllByRole("menuitemcheckbox").some((item) => item.ariaChecked === "true"),
    ).toBe(false);
  });

  it("sets a speed typed in the menu, and ignores text that is not a number", () => {
    const { controls } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Speed" }));
    const field = screen.getByRole("textbox", { name: "Custom" });
    fireEvent.change(field, { target: { value: "x" } });
    fireEvent.submit(field);
    fireEvent.change(field, { target: { value: "2.5" } });
    fireEvent.submit(field);
    expect(controls.setSpeed.mock.calls).toEqual([[2.5]]);
  });

  it("says why the frame the screen waits on cannot be shown", () => {
    setup({ frameError: { sequenceId: 30, reason: "corrupt slice" } });
    expect(screen.getByRole("alert").textContent).toBe("Frame 30 cannot be shown: corrupt slice");
  });
});
