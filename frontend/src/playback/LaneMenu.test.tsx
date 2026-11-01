import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { LaneStatusWire } from "../generated/api";
import type { SettingsApi } from "../settings";
import { LANE_SETTINGS } from "./fakeOpened";
import { LaneMenu } from "./LaneMenu";
import type { LaneState } from "./lanes";

afterEach(cleanup);

const CATALOGS = [
  { id: "built-in:standard", name: "Standard", description: "" },
  { id: "built-in:tracking", name: "Tracking", description: "" },
  { id: "user:night.json", name: "Standard", description: "" },
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

const ALL_FEATURES = 0b1_1111_1111;

function setup(
  lane: Partial<LaneState> = {},
  status: LaneStatusWire = READY,
  save: SettingsApi["save"] = vi.fn(async () => null),
) {
  const onChange = vi.fn();
  const onHide = vi.fn();
  const onApplyAll = vi.fn();
  const state: LaneState = {
    label: "DPM",
    source: 0,
    status,
    settings: LANE_SETTINGS,
    policyRevision: 1,
    pending: false,
    look: { themes: ["night"], features: ALL_FEATURES },
    ...lane,
  };
  const menu = (shown: LaneState) => (
    <FakeSettings save={save}>
      <LaneMenu
        lane={shown}
        catalogs={CATALOGS}
        onChange={onChange}
        onHide={onHide}
        onApplyAll={onApplyAll}
      />
    </FakeSettings>
  );
  const rendered = render(menu(state));
  const show = (change: Partial<LaneState>) => rendered.rerender(menu({ ...state, ...change }));
  return { onChange, onHide, onApplyAll, save, state, show, ...rendered };
}

const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const box = (name: string) => screen.getByRole("checkbox", { name }) as HTMLInputElement;

describe("lane menu", () => {
  it("has the Look, Filter and Timing tabs, opening on Look", () => {
    setup();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Look",
      "Filter",
      "Timing",
    ]);
    expect(screen.getByRole("tab", { name: "Look" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("combobox", { name: "Catalog" })).toBeTruthy();
    expect(screen.queryByRole("switch", { name: "Sticky" })).toBeNull();
    tab("Timing");
    expect(screen.getByRole("switch", { name: "Sticky" })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Catalog" })).toBeNull();
  });

  describe("look", () => {
    it("offers the catalogs, tells a user catalog that repeats a name by its file, and resets the theme with the catalog", () => {
      const { onChange } = setup({ settings: { ...LANE_SETTINGS, theme: "night" } });
      const names = [
        ...screen.getByRole("combobox", { name: "Catalog" }).querySelectorAll("option"),
      ];
      expect(names.map((option) => option.textContent)).toEqual([
        "Standard",
        "Tracking",
        "Standard (night.json)",
      ]);
      fireEvent.change(screen.getByRole("combobox", { name: "Catalog" }), {
        target: { value: "built-in:tracking" },
      });
      expect(onChange).toHaveBeenCalledWith({ catalog: "built-in:tracking", theme: null });
    });

    it("offers a theme only when the catalog declares some", () => {
      const { onChange, show } = setup();
      fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), {
        target: { value: "night" },
      });
      expect(onChange).toHaveBeenLastCalledWith({ theme: "night" });
      show({ look: { themes: [], features: ALL_FEATURES } });
      expect(screen.queryByRole("combobox", { name: "Theme" })).toBeNull();
      show({
        settings: { ...LANE_SETTINGS, theme: "night" },
        look: { themes: ["night"], features: 0 },
      });
      fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), { target: { value: "" } });
      expect(onChange).toHaveBeenLastCalledWith({ theme: null });
    });

    it("switches the catalog's features on and off by name, and lists only those it has", () => {
      const { onChange, show } = setup();
      expect(screen.getAllByRole("checkbox").map((b) => b.closest("label")?.textContent)).toEqual([
        "Object outlines",
        "Object IDs",
        "Class names",
        "Scores",
        "Confidence",
        "Speed arrows",
        "Movement badges",
        "Attributes",
        "Relations",
      ]);
      fireEvent.click(box("Class names"));
      expect(onChange).toHaveBeenLastCalledWith({ hidden: 0b100 });
      show({
        settings: { ...LANE_SETTINGS, hidden: 0b101 },
        look: { themes: [], features: 0b111 },
      });
      expect(screen.getAllByRole("checkbox")).toHaveLength(3);
      expect(box("Object outlines").checked).toBe(false);
      expect(box("Object IDs").checked).toBe(true);
      fireEvent.click(box("Object outlines"));
      expect(onChange).toHaveBeenLastCalledWith({ hidden: 0b100 });
    });

    it("saves the lane's catalog as the Personal default, unless it failed or already is", async () => {
      const { save, show } = setup();
      fireEvent.click(screen.getByRole("button", { name: "Use as default" }));
      expect(save).toHaveBeenCalledWith({
        values: { ...DEFAULT_SETTINGS.values, "overlays.default_catalog": "built-in:standard" },
        shortcuts: DEFAULT_SETTINGS.shortcuts,
      });
      show({ catalogProblem: "/layers: unknown" });
      const use = screen.getByRole("button", { name: "Use as default" }) as HTMLButtonElement;
      expect(use.disabled).toBe(true);
      show({ look: undefined });
      expect(use.disabled).toBe(true);
      cleanup();
      setup({}, READY, async () => ({ reason: "no room", location: "" }));
      fireEvent.click(screen.getByRole("button", { name: "Use as default" }));
      await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("no room"));
    });
  });

  describe("filter", () => {
    it("toggles one class against its default, shows all, and resets", () => {
      const { onChange, state, show } = setup();
      tab("Filter");
      expect(box("Pedestrian").checked).toBe(true);
      expect(box("Static person").checked).toBe(false);
      fireEvent.click(box("Pedestrian"));
      expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: false } });
      fireEvent.click(screen.getByRole("button", { name: "Show all" }));
      expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: true, static: true } });
      expect((screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(
        true,
      );
      show({ settings: { ...state.settings, filter: { static: true } } });
      expect(box("Static person").checked).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: "Reset" }));
      expect(onChange).toHaveBeenLastCalledWith({ filter: {} });
    });

    it("has no class section for a lane whose file calls for no classes", () => {
      setup({}, { ...READY, filter: [] });
      tab("Filter");
      expect(screen.queryByRole("group", { name: "Classes" })).toBeNull();
      expect(screen.getByRole("textbox", { name: "Filter expression" })).toBeTruthy();
    });

    it("checks the expression at every keystroke and applies it on Enter or blur only when it compiles", () => {
      const { onChange } = setup();
      tab("Filter");
      const field = screen.getByRole("textbox", { name: "Filter expression" }) as HTMLInputElement;
      expect(screen.queryByRole("alert")).toBeNull();
      fireEvent.change(field, { target: { value: "score >=" } });
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
      expect(screen.getAllByRole("alert")[0].textContent).not.toBe("");
      fireEvent.keyDown(field, { key: "Enter" });
      fireEvent.blur(field);
      expect(onChange).not.toHaveBeenCalled();
      fireEvent.change(field, { target: { value: "nonsense > 1" } });
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
      fireEvent.change(field, { target: { value: "score >= 0.4" } });
      expect(screen.queryByRole("alert")).toBeNull();
      expect(onChange).not.toHaveBeenCalled();
      fireEvent.keyDown(field, { key: "Enter" });
      expect(onChange).toHaveBeenLastCalledWith({ expression: "score >= 0.4" });
      fireEvent.change(field, { target: { value: "score >= 0.5" } });
      fireEvent.blur(field);
      expect(onChange).toHaveBeenLastCalledWith({ expression: "score >= 0.5" });
    });

    it("clears the expression with an empty field and does not report an unchanged one", () => {
      const { onChange } = setup({ settings: { ...LANE_SETTINGS, expression: "score >= 0.4" } });
      tab("Filter");
      const field = screen.getByRole("textbox", { name: "Filter expression" }) as HTMLInputElement;
      expect(field.value).toBe("score >= 0.4");
      fireEvent.blur(field);
      expect(onChange).not.toHaveBeenCalled();
      fireEvent.change(field, { target: { value: "" } });
      fireEvent.keyDown(field, { key: "Enter" });
      expect(onChange).toHaveBeenLastCalledWith({ expression: "" });
    });
  });

  describe("timing", () => {
    it("switches sticky on, and sets its timeout and opacity when the field is left", () => {
      const { onChange, state, show } = setup();
      tab("Timing");
      expect(screen.queryByRole("spinbutton", { name: "Timeout (ms)" })).toBeNull();
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
      tab("Timing");
      const timeout = screen.getByRole("spinbutton", { name: "Timeout (ms)" });
      fireEvent.change(timeout, { target: { value: "250000" } });
      fireEvent.blur(timeout);
      expect(onChange).toHaveBeenLastCalledWith({ sticky: { ...sticky, timeout_ms: 100_000 } });
    });

    it("offers sync only for a lane keyed by time", () => {
      setup();
      tab("Timing");
      expect(screen.queryByRole("combobox", { name: "Sync" })).toBeNull();
      cleanup();
      const { onChange } = setup({}, { ...READY, clock: "timestamp" });
      tab("Timing");
      fireEvent.change(screen.getByRole("combobox", { name: "Sync" }), {
        target: { value: "exact" },
      });
      expect(onChange).toHaveBeenLastCalledWith({ tolerance_ms: null });
      cleanup();
      const exact = setup(
        { settings: { ...LANE_SETTINGS, tolerance_ms: null } },
        { ...READY, clock: "timestamp" },
      );
      tab("Timing");
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
      tab("Timing");
      const within = screen.getByRole("spinbutton", { name: "Within (ms)" });
      fireEvent.change(within, { target: { value: "120" } });
      fireEvent.blur(within);
      expect(onChange).toHaveBeenLastCalledWith({ tolerance_ms: 120 });
    });
  });

  it("hides the lane and applies its settings to all lanes from any tab", () => {
    const { onHide, onApplyAll } = setup();
    tab("Timing");
    fireEvent.click(screen.getByRole("button", { name: "Hide lane" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply to all lanes" }));
    expect(onHide).toHaveBeenCalledTimes(1);
    expect(onApplyAll).toHaveBeenCalledTimes(1);
  });
});
