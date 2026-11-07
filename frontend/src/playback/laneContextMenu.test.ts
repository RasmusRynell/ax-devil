import { describe, expect, it } from "vite-plus/test";
import { laneContextEntries } from "./laneContextMenu";

const ALLOWED = { host: true, content: true, view: true, app: true };

describe("lane context menu", () => {
  it("lists the lane's actions in Python's order, with Hide set apart last", () => {
    expect(
      laneContextEntries(ALLOWED, {}, true, false).map((entry) =>
        entry.kind === "action" ? entry.label : entry.kind,
      ),
    ).toEqual([
      "Export video",
      "Toggle Media Tools Panel",
      "Toggle Lane Fullscreen",
      "Add Bookmark",
      "Reset Zoom",
      "separator",
      "Hide",
    ]);
  });

  it("offers Export video only once the clicked lane's render catalog has loaded", () => {
    const exportEntry = (cold: boolean) =>
      laneContextEntries(ALLOWED, {}, false, cold).find(
        (entry) => entry.kind === "action" && entry.id === "export-video",
      );
    expect(exportEntry(false)).toMatchObject({
      disabled: false,
      tip: "Export video with overlays",
    });
    expect(exportEntry(true)).toMatchObject({
      disabled: true,
      reason: "Export is available once the render catalog has loaded",
    });
  });
});
