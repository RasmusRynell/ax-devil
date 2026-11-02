import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { InspectionView } from "./contract";
import { FRESH_MEMORY } from "./fields";
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

type Shown = Exclude<InspectionView, { state: "none" }>;

/** The inspector with the memory a viewer would hold for it. */
function Viewer({ inspection, onClose }: { inspection: Shown; onClose: () => void }) {
  const [memory, onMemory] = useState(FRESH_MEMORY);
  return (
    <Inspector inspection={inspection} onClose={onClose} memory={memory} onMemory={onMemory} />
  );
}

function show(inspection: Shown, onClose = vi.fn()) {
  const view = render(<Viewer inspection={inspection} onClose={onClose} />);
  return Object.assign(onClose, {
    next: (other: Shown) => view.rerender(<Viewer inspection={other} onClose={onClose} />),
  });
}

const group = (name: string) => screen.getByRole("button", { name });

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
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("lists the fields in groups, the bulky ones folded, and leaves out empty groups", () => {
    show(ready(FIELDS));
    const captions = screen.getAllByRole("button", { name: /^(Classification|Debug|Raw)$/ });
    expect(captions.map((caption) => caption.getAttribute("aria-expanded"))).toEqual([
      "true",
      "false",
      "false",
    ]);
    expect(screen.queryByRole("button", { name: "Motion" })).toBeNull();
    expect(screen.queryByText("flags.0")).toBeNull();
    fireEvent.click(group("Debug"));
    expect(screen.getByText("Source")).toBeTruthy();
    expect(screen.getByText("Computed")).toBeTruthy();
    expect(screen.getByText("flags.0")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("keeps the previous fields while the next sample's are on their way", () => {
    show(ready(FIELDS, "loading"));
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(group("Classification")).toBeTruthy();
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

  it("shows numbers rounded and copies them, and the Raw group, whole", () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show(
      ready([
        ...FIELDS,
        { group: "motion", path: ["speed"], value: 0.123456 },
        { group: "raw", path: ["blob"], value: "x".repeat(300) },
      ]),
    );
    expect(screen.getByText("0.1235")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy speed" }));
    expect(writeText).toHaveBeenLastCalledWith("0.123456");
    fireEvent.click(screen.getByRole("button", { name: "Copy class" }));
    expect(writeText).toHaveBeenLastCalledWith("car");
    fireEvent.click(group("Raw"));
    fireEvent.click(screen.getByRole("button", { name: "Copy Raw" }));
    expect(writeText).toHaveBeenLastCalledWith(
      `{\n  "note": null,\n  "blob": "${"x".repeat(300)}"\n}`,
    );
  });

  it("pins a field for the next object, says when it is missing, and drops the pinned list on the last unpin", () => {
    const view = show(ready(FIELDS));
    fireEvent.click(screen.getByRole("button", { name: "Pin class" }));
    const pinned = within(screen.getByRole("region", { name: "Pinned" }));
    expect(pinned.getByText("classification.class")).toBeTruthy();
    expect(pinned.getByText("car")).toBeTruthy();

    view.next(ready([{ group: "motion", path: ["speed"], value: 2 }]));
    const missing = within(screen.getByRole("region", { name: "Pinned" }));
    expect(missing.getByText("unavailable")).toBeTruthy();
    expect(missing.queryByRole("button", { name: /^Copy/ })).toBeNull();
    expect(screen.getByText("speed")).toBeTruthy();

    fireEvent.contextMenu(missing.getByText("classification.class"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpin field" }));
    expect(screen.queryByRole("region", { name: "Pinned" })).toBeNull();
    expect(screen.queryByRole("separator")).toBeNull();
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
