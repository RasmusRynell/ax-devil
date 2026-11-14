import { describe, expect, it } from "vite-plus/test";
import {
  chosen,
  idleScan,
  scanFinished,
  scanInvalidated,
  toggled,
  toggledAll,
  type ScanView,
} from "./scan";

const found = (...names: string[]) =>
  names.map((name) => ({ value: name, label: name, detail: "" }));
const selectedOf = (view: ScanView) => [...view.selected].sort();

describe("scan selection", () => {
  it("selects everything found and names it, unless restored settings named no list", () => {
    const fresh = scanFinished(idleScan(undefined), found("a", "b"));
    expect(selectedOf(fresh)).toEqual(["a", "b"]);
    expect(chosen(fresh)).toEqual(["a", "b"]);
    const restored = scanFinished(idleScan(undefined, true), found("a", "b"));
    expect(chosen(restored)).toBeUndefined();
    expect(chosen(toggled(restored, "b"))).toEqual(["a"]);
    expect(chosen(toggled(toggled(restored, "b"), "b"))).toBeUndefined();
  });

  it("keeps a saved list as the selection, and the saved names not found as missing", () => {
    const view = scanFinished(idleScan(["b", "gone"]), found("a", "b"));
    expect(view.missing).toEqual(["gone"]);
    expect(chosen(view)).toEqual(["b", "gone"]);
    // A saved list stays a list, even when it names everything.
    expect(chosen(scanFinished(idleScan(["a", "b"]), found("a", "b")))).toEqual(["a", "b"]);
  });

  it("forgets the saved selection when the root changes, so the next scan checks everything", () => {
    const view = scanInvalidated(scanFinished(idleScan(["b", "gone"]), found("a", "b")));
    expect(selectedOf(view)).toEqual([]);
    const next = scanFinished(view, found("a", "c"));
    expect(selectedOf(next)).toEqual(["a", "c"]);
    expect(chosen(next)).toEqual(["a", "c"]);
  });

  it("checks everything, or unchecks it when it was all checked", () => {
    const view = scanFinished(idleScan(["a"]), found("a", "b"));
    expect(selectedOf(toggledAll(view))).toEqual(["a", "b"]);
    expect(selectedOf(toggledAll(toggledAll(view)))).toEqual([]);
  });
});
