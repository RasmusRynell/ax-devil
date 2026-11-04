import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import type { BasisWire } from "../generated/api";
import { LANE_SETTINGS } from "./fakeOpened";
import { box, READY, setup, tab } from "./laneMenuRig";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("lane menu timing", () => {
  it("shows how the video's frames are spaced and how the lane's samples land on them", () => {
    const ended = {
      ...READY,
      alignment: {
        basis: "Time" as const,
        samples: 334,
        matched: 300,
        exact: 173,
        end_gap_us: -19_080_000,
        ends_far: true,
        verdict: "ends_far" as const,
      },
    };
    const variable = { variable: true, periodsUs: [40_000, 80_000, 33_333, 20_000, 10_000] };
    setup({}, ended, undefined, variable);
    tab("Timing");
    const group = screen.getByRole("group", { name: "Alignment" });
    expect(group.textContent).toContain("Video: variable, 40 ms, 80 ms, 33.333 ms");
    expect(group.textContent).toContain(
      "Overlay: 300 of 334 on a frame (90%, exact 173, tol 50 ms)",
    );
    expect(screen.getByText("Ends 19.1 s before the video").className).toContain("text-caution");
  });

  it("a frame-matched lane's alignment shows its sequence count, with no exact count or end gap", () => {
    const byFrame = {
      ...READY,
      alignment: {
        basis: "Frame" as const,
        samples: 3,
        matched: 3,
        exact: 3,
        end_gap_us: null,
        ends_far: false,
        verdict: "good" as const,
      },
    };
    setup({}, byFrame);
    tab("Timing");
    const group = screen.getByRole("group", { name: "Alignment" });
    expect(group.textContent).toContain("Overlay: 3 of 3 on a frame (100%, sequence 3)");
    expect(group.textContent).not.toMatch(/exact|tol|Ends/);
  });

  it("switches sticky on, and sets its timeout and opacity when the field is left", () => {
    const { onChange, state, show } = setup();
    tab("Timing");
    fireEvent.click(screen.getByRole("switch", { name: "Sticky" }));
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: { ...state.settings.sticky, enabled: true },
    });
    const sticky = { enabled: true, timeout_ms: 2050, opacity: 1 };
    show({ settings: { ...state.settings, sticky } });
    const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" });
    fireEvent.change(timeout, { target: { value: "500" } });
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: { ...sticky, timeout_ms: 500 },
      kept_timeout_ms: 500,
    });
    const opacity = screen.getByRole("spinbutton", { name: "Reused overlay opacity" });
    fireEvent.change(opacity, { target: { value: "0.4" } });
    fireEvent.keyDown(opacity, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, opacity: 0.4 } });
  });

  it("unchecking expiry keeps the timeout, disabled, and checking it again restores it", () => {
    const sticky = { enabled: true, timeout_ms: 700, opacity: 1 };
    const { onChange, state, show } = setup({
      settings: { ...LANE_SETTINGS, sticky, kept_timeout_ms: 700 },
    });
    tab("Timing");
    const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" }) as HTMLInputElement;
    expect(timeout.step).toBe("1");
    expect(
      (screen.getByRole("spinbutton", { name: "Reused overlay opacity" }) as HTMLInputElement).step,
    ).toBe("0.05");
    fireEvent.click(box("Expire after (ms)"));
    const unlimited = { ...state.settings, sticky: { ...sticky, timeout_ms: null } };
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: unlimited.sticky,
      kept_timeout_ms: 700,
    });
    const kept = { ...unlimited, kept_timeout_ms: 700 };
    show({ settings: kept });
    expect(timeout.disabled).toBe(true);
    expect(timeout.value).toBe("700");
    cleanup();
    const reopened = setup({ settings: kept });
    tab("Timing");
    expect(
      (screen.getByRole("spinbutton", { name: "Timeout (ms)" }) as HTMLInputElement).value,
    ).toBe("700");
    fireEvent.click(box("Expire after (ms)"));
    expect(reopened.onChange).toHaveBeenLastCalledWith({ sticky, kept_timeout_ms: 700 });
    reopened.show({ settings: { ...kept, sticky: { ...sticky, enabled: false } } });
    expect(box("Expire after (ms)").disabled).toBe(true);
    expect(
      (screen.getByRole("spinbutton", { name: "Timeout (ms)" }) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("spinbutton", { name: "Reused overlay opacity" }) as HTMLInputElement)
        .disabled,
    ).toBe(true);
  });

  it("saves the lane's sticky settings as the Personal default, and says so when they already are", () => {
    const sticky = { enabled: false, timeout_ms: null, opacity: 0.4 };
    const { save } = setup({ settings: { ...LANE_SETTINGS, sticky } });
    tab("Timing");
    fireEvent.click(screen.getByRole("button", { name: "Make default" }));
    expect(save).toHaveBeenCalledWith({
      values: { ...DEFAULT_SETTINGS.values, "overlays.default_sticky": sticky },
      shortcuts: DEFAULT_SETTINGS.shortcuts,
    });
    cleanup();
    const saved = DEFAULT_SETTINGS.values["overlays.default_sticky"] as typeof sticky;
    setup({ settings: { ...LANE_SETTINGS, sticky: saved } });
    tab("Timing");
    expect(screen.getByText("Default for new views")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
  });

  it("limits the timeout to what the backend accepts", () => {
    const sticky = { enabled: true, timeout_ms: 1, opacity: 1 };
    const { onChange } = setup({ settings: { ...LANE_SETTINGS, sticky } });
    tab("Timing");
    const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" });
    fireEvent.change(timeout, { target: { value: "250000" } });
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: { ...sticky, timeout_ms: 100_000 },
      kept_timeout_ms: 100_000,
    });
    fireEvent.change(timeout, { target: { value: "500.5" } });
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: { ...sticky, timeout_ms: 501 },
      kept_timeout_ms: 501,
    });
  });

  it("offers a basis only when the file offers two, and sync, savable as the default, only under time", () => {
    setup();
    tab("Timing");
    expect(screen.queryByRole("combobox", { name: "Match by" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Sync" })).toBeNull();
    cleanup();
    const both = {
      ...READY,
      bases: ["Frame", "Time"] as BasisWire[],
      default_basis: "Frame" as const,
    };
    const { onChange } = setup({}, both);
    tab("Timing");
    expect(screen.queryByRole("combobox", { name: "Sync" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Match by" }), {
      target: { value: "Time" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ basis: "Time" });
    cleanup();
    const timed = setup({ settings: { ...LANE_SETTINGS, basis: "Time", tolerance_ms: 80 } }, both);
    tab("Timing");
    fireEvent.change(screen.getByRole("combobox", { name: "Sync" }), {
      target: { value: "exact" },
    });
    expect(timed.onChange).toHaveBeenLastCalledWith({ tolerance_ms: null });
    const previous = screen.getByRole("spinbutton", { name: "Within (ms)" });
    fireEvent.change(previous, { target: { value: "120" } });
    fireEvent.blur(previous);
    expect(timed.onChange).toHaveBeenLastCalledWith({ tolerance_ms: 120 });
    const sync = screen.getByRole("group", { name: "Sync" });
    fireEvent.click(within(sync).getByRole("button", { name: "Make default" }));
    expect(timed.save).toHaveBeenCalledWith({
      values: { ...DEFAULT_SETTINGS.values, "overlays.default_sync": { tolerance_ms: 80 } },
      shortcuts: DEFAULT_SETTINGS.shortcuts,
    });
    cleanup();
    setup({ settings: { ...LANE_SETTINGS, basis: "Time" } });
    tab("Timing");
    expect(screen.queryByRole("combobox", { name: "Match by" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Sync" })).toBeNull();
  });
});
