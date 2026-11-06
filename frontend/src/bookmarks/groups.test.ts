import { expect, it } from "vite-plus/test";
import type { BookmarkGroupInfo, BookmarkInfo } from "../generated/api";
import { countOf, filterGroups, labelOf, subtitleOf } from "./groups";

const mark = (id: string, frame: number, kind: BookmarkInfo["kind"], note = ""): BookmarkInfo => ({
  id,
  frame,
  time_us: frame * 40_000,
  kind,
  note,
});

const group = (
  item_name: string,
  bookmarks: BookmarkInfo[],
  reviewed = false,
): BookmarkGroupInfo => ({
  key: item_name,
  item: item_name,
  item_name,
  entry: "",
  target: null,
  reviewed,
  standing: { status: "Pending" },
  bookmarks,
});

const GROUPS = [
  group("clip.mp4", [mark("a", 1, "issue", "Blur"), mark("b", 2, "note")]),
  group("other.mp4", [mark("c", 3, "issue", "blur too")]),
  group("done.mp4", [], true),
];

it("names a bookmark by its text or its frame and says where it is under it", () => {
  expect(labelOf(mark("a", 75, "note"))).toBe("Frame 75");
  expect(labelOf(mark("a", 75, "note", "Blur"))).toBe("Blur");
  expect(subtitleOf(mark("a", 75, "question"))).toBe("0:03.00 · frame 75 · Question");
  expect(countOf(GROUPS)).toBe(3);
});

it("filters by kind and text, a matching title keeps all its bookmarks, and a reviewed group stays only unfiltered", () => {
  const names = (query: string, kind: BookmarkInfo["kind"] | null) =>
    filterGroups(GROUPS, query, kind).map((found) => [
      found.item_name,
      found.bookmarks.map((bookmark) => bookmark.id),
    ]);
  expect(names("", null)).toEqual([
    ["clip.mp4", ["a", "b"]],
    ["other.mp4", ["c"]],
    ["done.mp4", []],
  ]);
  expect(names("  BLUR ", null)).toEqual([
    ["clip.mp4", ["a"]],
    ["other.mp4", ["c"]],
  ]);
  expect(names("clip", "note")).toEqual([["clip.mp4", ["b"]]]);
  expect(names("done", null)).toEqual([["done.mp4", []]]);
  expect(names("done", "issue")).toEqual([]);
});
