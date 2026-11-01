import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { InspectionView } from "./contract";
import { Inspector } from "./Inspector";
import type { FieldWire } from "./protocol";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SELECTION = { lane: 0, source: 0, entity: "7" };
const SUMMARY = { entity: "7", className: "car", score: 0.8123, confidence: null };
const FIELDS: FieldWire[] = [
  { group: "classification", path: ["class"], value: "car" },
  { group: "debug", origin: "source", path: ["debug", "frame"], value: 12 },
  { group: "debug", origin: "decoder", path: ["flags", "0"], value: true },
  { group: "raw", path: ["note"], value: null },
];

function show(inspection: Exclude<InspectionView, { state: "none" }>, onClose = vi.fn()) {
  render(<Inspector inspection={inspection} onClose={onClose} />);
  return onClose;
}

const ready = (
  fields: readonly FieldWire[],
  state: "loading" | "ready" | "unavailable" = "ready",
): Extract<InspectionView, { fields: readonly FieldWire[] }> => ({
  state,
  selection: SELECTION,
  summary: SUMMARY,
  fields,
  truncated: false,
});

describe("inspector", () => {
  it("shows the summary and, until a reply, Loading… without any group", () => {
    show(ready([], "loading"));
    expect(screen.getByRole("heading", { name: "7 car" })).toBeTruthy();
    expect(screen.getByText("score 0.81")).toBeTruthy();
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("lists the fields in groups, Debug ones with who produced them, and leaves out empty groups", () => {
    show(ready(FIELDS));
    const groups = screen.getAllByRole("region").map((group) => group.getAttribute("aria-label"));
    expect(groups).toEqual(["Classification", "Debug", "Raw"]);
    const debug = within(screen.getByRole("region", { name: "Debug" }));
    expect(debug.getByText("Source")).toBeTruthy();
    expect(debug.getByText("Computed")).toBeTruthy();
    expect(debug.getByText("flags.0")).toBeTruthy();
    expect(screen.getByText("null")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("keeps the previous fields while the next sample's are on their way", () => {
    show(ready(FIELDS, "loading"));
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Classification" })).toBeTruthy();
  });

  it("says when the object is not in the frame or the details are not available", () => {
    show({ state: "absent", selection: SELECTION });
    expect(screen.getByRole("heading", { name: "7" })).toBeTruthy();
    expect(screen.getByText("Not in this frame")).toBeTruthy();
    cleanup();
    show(ready([], "unavailable"));
    expect(screen.getByText("Details are not available")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "7 car" })).toBeTruthy();
  });

  it("copies a value", () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show(ready(FIELDS));
    fireEvent.click(screen.getByRole("button", { name: "Copy car" }));
    expect(writeText).toHaveBeenCalledWith("car");
  });

  it("cuts a long text until expanded, and copies all of it", () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const long = "x".repeat(300);
    show(ready([{ group: "raw", path: ["blob"], value: long }]));
    const shown = () => screen.getByRole("region", { name: "Raw" }).textContent ?? "";
    expect(shown()).not.toContain(long);
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(shown()).toContain(long);
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(shown()).not.toContain(long);
    fireEvent.click(screen.getByTitle("Copy"));
    expect(writeText).toHaveBeenCalledWith(long);
  });

  it("says when fields were left out", () => {
    show({ ...ready(FIELDS), truncated: true });
    expect(screen.getByText("Some fields are not listed")).toBeTruthy();
  });

  it("closes from its button", () => {
    const onClose = show(ready(FIELDS));
    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
