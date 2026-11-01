import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { LaneStatusWire } from "../generated/api";
import { LANE_SETTINGS } from "./fakeOpened";
import { LaneMenu } from "./LaneMenu";
import type { LaneState } from "./lanes";

afterEach(cleanup);

const CATALOGS = [
  { id: "built-in:standard", name: "Standard", description: "" },
  { id: "built-in:tracking", name: "Tracking", description: "" },
];

const READY: LaneStatusWire = {
  status: "Ready",
  clock: "frame",
  filter: [
    { id: "pedestrian", label: "Pedestrian", default_enabled: true },
    { id: "static", label: "Static person", default_enabled: false },
  ],
  warnings: [],
  samples: 12,
  data_revision: 1,
  mean_sample_bytes: 100,
};

function setup(lane: Partial<LaneState> = {}, status: LaneStatusWire = READY) {
  const onChange = vi.fn();
  const onHide = vi.fn();
  const state: LaneState = {
    label: "DPM",
    source: 0,
    status,
    settings: LANE_SETTINGS,
    policyRevision: 1,
    pending: false,
    ...lane,
  };
  const rendered = render(
    <LaneMenu lane={state} catalogs={CATALOGS} onChange={onChange} onHide={onHide} />,
  );
  return { onChange, onHide, state, ...rendered };
}

describe("lane menu", () => {
  it("offers the backend's catalogs and changes the lane's catalog", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByRole("combobox", { name: "Catalog" }), {
      target: { value: "built-in:tracking" },
    });
    expect(onChange).toHaveBeenCalledWith({ catalog: "built-in:tracking" });
  });

  it("toggles one class against its default, shows all, and resets", () => {
    const { onChange, state, rerender, onHide } = setup();
    const box = (name: string) => screen.getByRole("checkbox", { name }) as HTMLInputElement;
    expect(box("Pedestrian").checked).toBe(true);
    expect(box("Static person").checked).toBe(false);
    fireEvent.click(box("Pedestrian"));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: false } });
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: true, static: true } });
    expect((screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    rerender(
      <LaneMenu
        catalogs={CATALOGS}
        lane={{ ...state, settings: { ...state.settings, filter: { static: true } } }}
        onChange={onChange}
        onHide={onHide}
      />,
    );
    expect(box("Static person").checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onChange).toHaveBeenLastCalledWith({ filter: {} });
  });

  it("has no class section for a lane whose file calls for no classes", () => {
    setup({}, { ...READY, filter: [] });
    expect(screen.queryByRole("group", { name: "Classes" })).toBeNull();
  });

  it("switches sticky on, and sets its timeout and opacity when the field is left", () => {
    const { onChange, state, rerender, onHide } = setup();
    expect(screen.queryByRole("spinbutton", { name: "Timeout (ms)" })).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "Sticky" }));
    expect(onChange).toHaveBeenLastCalledWith({
      sticky: { ...state.settings.sticky, enabled: true },
    });
    const sticky = { enabled: true, timeout_ms: 2050, opacity: 1 };
    rerender(
      <LaneMenu
        catalogs={CATALOGS}
        lane={{ ...state, settings: { ...state.settings, sticky } }}
        onChange={onChange}
        onHide={onHide}
      />,
    );
    const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" });
    fireEvent.change(timeout, { target: { value: "500" } });
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, timeout_ms: 500 } });
    fireEvent.change(timeout, { target: { value: "" } });
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, timeout_ms: null } });
    const opacity = screen.getByRole("spinbutton", { name: "Opacity" });
    fireEvent.change(opacity, { target: { value: "0.4" } });
    fireEvent.keyDown(opacity, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, opacity: 0.4 } });
  });

  it("limits the timeout to what the backend accepts", () => {
    const sticky = { enabled: true, timeout_ms: 1, opacity: 1 };
    const { onChange } = setup({ settings: { ...LANE_SETTINGS, sticky } });
    const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" });
    fireEvent.change(timeout, { target: { value: "250000" } });
    fireEvent.blur(timeout);
    expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, timeout_ms: 100_000 } });
  });

  it("offers sync only for a lane keyed by time", () => {
    setup();
    expect(screen.queryByRole("combobox", { name: "Sync" })).toBeNull();
    cleanup();
    const { onChange } = setup({}, { ...READY, clock: "timestamp" });
    fireEvent.change(screen.getByRole("combobox", { name: "Sync" }), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ tolerance_ms: null });
    cleanup();
    const exact = setup(
      { settings: { ...LANE_SETTINGS, tolerance_ms: null } },
      { ...READY, clock: "timestamp" },
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Sync" }), {
      target: { value: "previous" },
    });
    expect(exact.onChange).toHaveBeenLastCalledWith({ tolerance_ms: 50 });
  });

  it("sets how far back a previous sample may be", () => {
    const { onChange } = setup(
      { settings: { ...LANE_SETTINGS, tolerance_ms: 50 } },
      { ...READY, clock: "timestamp" },
    );
    const within = screen.getByRole("spinbutton", { name: "Within (ms)" });
    fireEvent.change(within, { target: { value: "120" } });
    fireEvent.blur(within);
    expect(onChange).toHaveBeenLastCalledWith({ tolerance_ms: 120 });
  });

  it("hides the lane", () => {
    const { onHide } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Hide lane" }));
    expect(onHide).toHaveBeenCalledTimes(1);
  });
});
