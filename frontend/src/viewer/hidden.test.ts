import { describe, expect, it } from "vite-plus/test";
import type { RowInfo } from "../generated/api";
import { hiddenOf } from "./hidden";

const row = (kind: RowInfo["kind"], label: string, entry: string | null, hidden: boolean) =>
  ({ key: label, kind, label, status: { status: "Ready" }, hidden, entry }) as RowInfo;

describe("hidden", () => {
  it("reads hidden entries, videos and lanes from the rows", () => {
    const hidden = hiddenOf([
      row("item", "clip.mp4", "clip", true),
      row("entry", "seq-1", "list/seq-1", true),
      row("entry", "seq-2", "list/seq-2", false),
      row("lane", "GT", "list/seq-2", true),
      row("lane", "DPM", "list/seq-2", false),
    ]);
    expect(["clip", "list/seq-1", "list/seq-2"].map((id) => hidden.entry(id))).toEqual([
      true,
      true,
      false,
    ]);
    expect(hidden.lane("list/seq-2", "GT")).toBe(true);
    expect(hidden.lane("list/seq-2", "DPM")).toBe(false);
    expect(hidden.lane("list/seq-1", "GT")).toBe(false);
  });
});
