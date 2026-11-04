import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { OPEN_RANGES } from "../present";
import { LANE_SETTINGS } from "./fakeOpened";
import { READY, box, setup, tab } from "./laneMenuRig";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("lane menu filter", () => {
  it("toggles one class against its default, shows all and none", () => {
    const { onChange } = setup();
    tab("Filter");
    expect(box("Pedestrian").checked).toBe(true);
    expect(box("Static person").checked).toBe(false);
    fireEvent.click(box("Pedestrian"));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: false } });
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: true, static: true } });
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { pedestrian: false, static: false } });
  });

  it("applies a score or confidence bound at every keystroke, reading a comma as a decimal point, and refuses what is not a number", () => {
    const { onChange, state, show } = setup();
    tab("Filter");
    const min = screen.getByRole("textbox", { name: "Score min" }) as HTMLInputElement;
    fireEvent.change(min, { target: { value: "0,5" } });
    const half = { ...OPEN_RANGES, score: { min: 0.5, max: null } };
    expect(onChange).toHaveBeenLastCalledWith({ ranges: half });
    show({ settings: { ...state.settings, ranges: half } });
    expect(min.value).toBe("0,5");
    show({ settings: { ...state.settings, ranges: { ...half, score: { min: 0.7, max: null } } } });
    expect(min.value).toBe("0.7");
    fireEvent.change(min, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ ranges: OPEN_RANGES });
    show({ settings: { ...state.settings, ranges: OPEN_RANGES } });
    fireEvent.change(screen.getByRole("textbox", { name: "Confidence max" }), {
      target: { value: " 0.9 " },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      ranges: { ...OPEN_RANGES, confidence: { min: null, max: 0.9 } },
    });
    show({ settings: { ...state.settings, ranges: half } });
    onChange.mockClear();
    for (const garbage of ["abc", "Infinity", "1e999"]) {
      fireEvent.change(min, { target: { value: garbage } });
      expect(min.value).toBe(garbage);
      expect(min.getAttribute("aria-invalid")).toBe("true");
    }
    expect(onChange).not.toHaveBeenCalled();
  });

  it("counts the controls that differ from their defaults, and resets classes, ranges and expression together", () => {
    const settings = {
      ...LANE_SETTINGS,
      filter: { pedestrian: false, static: false },
      ranges: { ...OPEN_RANGES, confidence: { min: null, max: 0.9 } },
      expression: "score >= 0.4",
    };
    const { onChange, show } = setup();
    tab("Filter");
    expect((screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    show({ settings });
    expect(screen.getByRole("tab", { name: "Filter 3" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onChange).toHaveBeenLastCalledWith({ filter: {}, ranges: OPEN_RANGES, expression: "" });
    // A class switched off and on again is no override, so nothing differs and Reset is off.
    show({ settings: { ...LANE_SETTINGS, filter: { pedestrian: false, other: true } } });
    fireEvent.click(box("Pedestrian"));
    expect(onChange).toHaveBeenLastCalledWith({ filter: { other: true } });
    show({ settings: { ...LANE_SETTINGS, filter: { other: true } } });
    expect(screen.getByRole("tab", { name: "Filter" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("does not apply a bound the expression cannot be combined with, or an expression the bounds cannot be added to", () => {
    const long = `score >= 0.4${" ".repeat(4070)}`;
    const { onChange, state, show } = setup({ settings: { ...LANE_SETTINGS, expression: long } });
    tab("Filter");
    const min = screen.getByRole("textbox", { name: "Confidence min" });
    fireEvent.change(min, { target: { value: "0.5" } });
    expect(min.getAttribute("aria-invalid")).toBe("true");
    expect(onChange).not.toHaveBeenCalled();
    const ranges = { ...OPEN_RANGES, confidence: { min: 0.5, max: null } };
    show({ settings: { ...state.settings, expression: "", ranges } });
    const field = screen.getByRole("textbox", { name: "Filter expression" });
    fireEvent.change(field, { target: { value: long } });
    expect(field.getAttribute("aria-invalid")).toBe("true");
    fireEvent.blur(field);
    expect(onChange).not.toHaveBeenCalled();
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
