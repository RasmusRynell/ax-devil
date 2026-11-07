import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import type { CatalogInfoWire } from "../generated/api";
import { LANE_SETTINGS } from "./fakeOpened";
import { ALL_FEATURES, CATALOGS, READY, box, setup, tab } from "./laneMenuRig";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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
        "Standard (default)",
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

    it("switches features on and off by name, and resets them only when one is off", () => {
      const { onChange, show } = setup();
      expect(screen.getAllByRole("checkbox")).toHaveLength(9);
      const reset = screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement;
      expect(reset.disabled).toBe(true);
      fireEvent.click(box("Class names"));
      expect(onChange).toHaveBeenLastCalledWith({ hidden: 0b100 });
      show({ settings: { ...LANE_SETTINGS, hidden: 0b101 } });
      expect(box("Object outlines").checked).toBe(false);
      expect(box("Object IDs").checked).toBe(true);
      expect(reset.disabled).toBe(false);
      fireEvent.click(reset);
      expect(onChange).toHaveBeenLastCalledWith({ hidden: 0 });
    });

    it("shows a feature the catalog does not draw disabled with why, and keeps its choice across catalogs", () => {
      const { show } = setup({ settings: { ...LANE_SETTINGS, hidden: 0b100 } });
      const classNames = () => box("Class names");
      expect(classNames().closest("label")?.title).toBe("Classification text.");
      show({ look: { themes: [], features: 0b11 } });
      expect(classNames().disabled).toBe(true);
      expect(classNames().checked).toBe(false);
      expect(classNames().closest("label")?.title).toBe("This catalog does not draw this detail.");
      expect(box("Object IDs").disabled).toBe(false);
      expect((screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(
        false,
      );
      show({ look: { themes: [], features: ALL_FEATURES } });
      expect(classNames().disabled).toBe(false);
      expect(classNames().checked).toBe(false);
    });

    it("saves the lane's catalog as the Personal default, unless it failed or already is", async () => {
      const { save, show } = setup({
        settings: { ...LANE_SETTINGS, catalog: "built-in:tracking" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Use as default" }));
      expect(save).toHaveBeenCalledWith({
        values: { ...DEFAULT_SETTINGS.values, "overlays.default_catalog": "built-in:tracking" },
        shortcuts: DEFAULT_SETTINGS.shortcuts,
      });
      show({ catalogProblem: "/layers: unknown" });
      const use = screen.getByRole("button", { name: "Use as default" }) as HTMLButtonElement;
      expect(use.disabled).toBe(true);
      // Still loading: the lane draws a fallback, which is not what would be saved.
      show({ catalogRevision: undefined });
      expect(use.disabled).toBe(true);
      show({ settings: LANE_SETTINGS });
      expect(use.disabled).toBe(true);
      cleanup();
      setup({ settings: { ...LANE_SETTINGS, catalog: "built-in:tracking" } }, READY, async () => ({
        reason: "no room",
        location: "",
      }));
      fireEvent.click(screen.getByRole("button", { name: "Use as default" }));
      await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("no room"));
    });

    it("names the default, hides catalogs that failed unless chosen, and says what is invalid or missing", () => {
      const listed: CatalogInfoWire[] = [
        ...CATALOGS.slice(0, 2),
        { id: "user:good.json", name: "Good", description: "" },
        { id: "user:broken.json", name: "Broken", description: "", problem: "/layers: unknown" },
        { id: "user:other.json", name: "Other", description: "", problem: "not JSON" },
      ];
      const labels = () =>
        [...screen.getByRole("combobox", { name: "Catalog" }).querySelectorAll("option")].map(
          (option) => option.textContent,
        );
      setup({}, READY, undefined, null, { catalogs: listed, defaultCatalog: "user:good.json" });
      expect(labels()).toEqual(["Standard", "Tracking", "Good (default)"]);
      cleanup();
      setup(
        { settings: { ...LANE_SETTINGS, catalog: "user:broken.json" } },
        READY,
        undefined,
        null,
        {
          catalogs: listed,
          defaultCatalog: "user:broken.json",
        },
      );
      // A default that does not compile is not the default: new views start from Standard.
      expect(labels()).toEqual(["Standard (default)", "Tracking", "Good", "broken.json (invalid)"]);
      cleanup();
      setup({ settings: { ...LANE_SETTINGS, catalog: "user:gone.json" } }, READY, undefined, null, {
        catalogs: listed,
      });
      expect(labels()).toEqual(["gone.json (missing)", "Standard (default)", "Tracking", "Good"]);
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

  it("applies nothing while the lane's catalog is loading or failed", () => {
    const { onApplyAll, show } = setup();
    const apply = screen.getByRole("button", { name: "Apply to all lanes" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(false);
    show({ catalogRevision: undefined });
    expect(apply.disabled).toBe(true);
    show({ catalogProblem: "/layers: unknown" });
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    expect(onApplyAll).not.toHaveBeenCalled();
  });
});
