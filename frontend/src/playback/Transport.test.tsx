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
  shown: { sequenceId: 29, timestampUs: 1_160_000, periodUs: 40_000 },
  lanes: [],
  catalogs: [],
  width: 64,
  height: 48,
  warnings: [],
  ...change,
});

/** `paused`: a seek moves the playhead at once, in the same update, as the session does when paused. */
function setup(change: Partial<PlaybackView> = {}, paused = false) {
  const controls = {
    togglePlay: vi.fn(),
    step: vi.fn(),
    seek: vi.fn((frame: number) => {
      if (paused)
        result.rerender(
          <Transport controls={controls} view={view({ ...change, playhead: frame })} />,
        );
    }),
    setSpeed: vi.fn(),
    frameTime: vi.fn((frame: number) => frame * 40_000),
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

  it("drags: the bar and readouts follow the pick, and the one seek is made when the pointer is let go", () => {
    const { controls, rerender } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
    const field = screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement;
    expect(bar.value).toBe("30");
    expect(bar.max).toBe("99");
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "64" } });
    fireEvent.change(bar, { target: { value: "65" } });
    // Playback goes on under the hand: its frames do not move the bar or the readouts.
    rerender(<Transport controls={controls} view={view({ playing: true, playhead: 40 })} />);
    expect(bar.value).toBe("65");
    expect(field.value).toBe("65");
    expect(screen.getByLabelText("Time").textContent).toBe("0:02.60");
    expect(controls.seek).not.toHaveBeenCalled();
    fireEvent.pointerUp(window, { pointerId: 1 });
    fireEvent.blur(bar);
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(controls.seek.mock.calls).toEqual([[65]]);
  });

  it("keeps the pick after the release until a frame is delivered, even as the clock runs on", () => {
    const { controls, rerender } = setup({ playing: true });
    const bar = screen.getByRole("slider", { name: "Timeline" });
    const field = screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement;
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "65" } });
    fireEvent.pointerUp(window, { pointerId: 1 });
    rerender(<Transport controls={controls} view={view({ playing: true, playhead: 66 })} />);
    expect(field.value).toBe("65");
    // Any frame delivered ends it: the sought one, or one the clock reached.
    const delivered = { sequenceId: 66, timestampUs: 2_640_000, periodUs: 40_000 };
    rerender(
      <Transport
        controls={controls}
        view={view({ playing: true, playhead: 66, shown: delivered })}
      />,
    );
    expect(field.value).toBe("66");
  });

  it("lets go of the pick when playback stops before the sought frame is delivered", () => {
    const { controls, rerender } = setup({ playing: true });
    const bar = screen.getByRole("slider", { name: "Timeline" });
    const field = screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement;
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "65" } });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(field.value).toBe("65");
    // Pausing puts the playhead back on the frame on screen, so nothing arrives.
    rerender(<Transport controls={controls} view={view({ playing: false })} />);
    expect(field.value).toBe("29");
  });

  it("lets go of the pick when its frame fails, even sought back from a later frame, or another seek is made", () => {
    const later = { sequenceId: 90, timestampUs: 3_600_000, periodUs: 40_000 };
    const { controls, rerender } = setup({ playhead: 90, shown: later }, true);
    const bar = screen.getByRole("slider", { name: "Timeline" });
    const field = screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement;
    const release = () => {
      fireEvent.pointerDown(bar, { pointerId: 1 });
      fireEvent.change(bar, { target: { value: "65" } });
      fireEvent.pointerUp(window, { pointerId: 1 });
      expect(field.value).toBe("65");
    };
    release();
    const frameError = { sequenceId: 65, reason: "corrupt" };
    rerender(
      <Transport controls={controls} view={view({ playhead: 65, shown: later, frameError })} />,
    );
    expect(field.value).toBe("90");
    rerender(<Transport controls={controls} view={view({ playhead: 90, shown: later })} />);
    release();
    fireEvent.click(screen.getByRole("button", { name: "Forward 1 frame" }));
    expect(field.value).toBe("90");
  });

  it("lets go of a paused pick when a step or seek elsewhere moves the playhead off its frame", () => {
    const { controls, rerender } = setup({}, true);
    const bar = screen.getByRole("slider", { name: "Timeline" });
    const field = screen.getByRole("textbox", { name: "Frame" }) as HTMLInputElement;
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "31" } });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(field.value).toBe("31");
    // A shortcut steps back before frame 31 arrives: nothing new is shown, and the pick would never settle.
    rerender(<Transport controls={controls} view={view({ playhead: 30 })} />);
    expect(field.value).toBe("29");
  });

  it("keeps a drag held when the frame field commits meanwhile, and still seeks once on release", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" });
    const field = screen.getByRole("textbox", { name: "Frame" });
    fireEvent.change(field, { target: { value: "7" } });
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.blur(field);
    fireEvent.change(bar, { target: { value: "64" } });
    fireEvent.change(bar, { target: { value: "65" } });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(controls.seek.mock.calls).toEqual([[7], [65]]);
  });

  it("is held by the pointer that pressed: another one's up or cancel does not seek", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" });
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.pointerDown(bar, { pointerId: 2 });
    fireEvent.pointerUp(window, { pointerId: 2 });
    fireEvent.pointerCancel(window, { pointerId: 2 });
    expect(controls.seek).not.toHaveBeenCalled();
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(controls.seek.mock.calls).toEqual([[30]]);
  });

  it("seeks a drag that ends in a cancel or in lost focus, once", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" }) as HTMLInputElement;
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "10" } });
    fireEvent.pointerCancel(window, { pointerId: 1 });
    fireEvent.pointerDown(bar, { pointerId: 1 });
    fireEvent.change(bar, { target: { value: "20" } });
    fireEvent.blur(bar);
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(controls.seek.mock.calls).toEqual([[10], [20]]);
  });

  it("seeks at once for a key on the seek bar", () => {
    const { controls } = setup();
    const bar = screen.getByRole("slider", { name: "Timeline" });
    fireEvent.change(bar, { target: { value: "31" } });
    fireEvent.change(bar, { target: { value: "32" } });
    expect(controls.seek.mock.calls).toEqual([[31], [32]]);
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

  it("goes to the frame typed, within the video (a negative one is the first), only when it is not where the seek bar is", () => {
    const { controls } = setup();
    const field = screen.getByRole("textbox", { name: "Frame" });
    const enter = (text: string) => {
      fireEvent.change(field, { target: { value: text } });
      fireEvent.keyDown(field, { key: "Enter" });
      fireEvent.blur(field);
    };
    enter("30");
    enter("500");
    enter("-3");
    enter("abc");
    fireEvent.change(field, { target: { value: "12" } });
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.blur(field);
    enter("7");
    expect(controls.seek.mock.calls).toEqual([[99], [0], [7]]);
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
