import type { EventRowWire } from "../generated/api";
// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { EventsPage } from "../inspect/fileEvents";
import { eventRow, lineFor, lineOf } from "./eventLine";

const at = (event: number, frame: number) => ({
  anchor: Array(32).fill(0) as EventRowWire["anchor"],
  event,
  frame,
  kind: "delete" as const,
  label: "Delete 1",
  objects: [],
});

/** Events on frames 2, 2, 5, 9, in rows `offset` on of a list of `matched`. */
const page = (offset: number, matched: number, frames = [2, 2, 5, 9]): EventsPage => ({
  revision: 1,
  total: matched,
  matched,
  offset,
  rows: frames.map((frame, index) => at(offset + index, frame)),
  located: null,
  frame: null,
  frameRow: null,
});

describe("the line between what has happened and what is to come", () => {
  it("goes after the events on frames up to the one on screen, as many as share it", () => {
    const whole = page(0, 4);
    expect(lineOf(whole, 1)).toBe(0);
    expect(lineOf(whole, 2)).toBe(2);
    expect(lineOf(whole, 4)).toBe(2);
    expect(lineOf(whole, 5)).toBe(3);
    expect(lineOf(whole, 40)).toBe(4);
    expect(lineOf(whole, undefined)).toBeNull();
  });

  it("is known from a page only when its rows reach both sides of the frame", () => {
    const middle = page(100, 400);
    // The rows before and after the page may be on either side.
    expect(lineOf(middle, 1)).toBeNull();
    expect(lineOf(middle, 40)).toBeNull();
    expect(lineOf(middle, 5)).toBe(103);
    // The first and last rows of the list are its ends.
    expect(lineOf(page(396, 400), 40)).toBe(400);
    expect(lineOf({ ...page(0, 0), rows: [] }, 3)).toBe(0);
  });

  it("is otherwise what the backend said for the frame it was asked about, even when the video has moved on since", () => {
    const far = { ...page(100, 400), frame: 40, frameRow: 250 };
    expect(lineFor(far, 40)).toBe(250);
    // The answer came after the frame had passed: it still says where the rows to load are.
    expect(lineFor(far, 41)).toBe(250);
    expect(lineFor({ ...far, frameRow: null }, 41)).toBeNull();
    expect(lineFor(page(0, 4), 5)).toBe(3);
  });

  it("finds an event's row from the backend, else from the rows loaded, else from where it was", () => {
    const loaded = page(100, 400);
    expect(eventRow(null, loaded, null)).toEqual({ row: null, near: null });
    expect(eventRow(102, loaded, null)).toEqual({ row: 102, near: 102 });
    expect(eventRow(300, { ...loaded, located: { event: 300, row: 299 } }, null)).toEqual({
      row: 299,
      near: 299,
    });
    expect(eventRow(300, loaded, { event: 300, row: 280 })).toEqual({ row: null, near: 280 });
    expect(eventRow(300, loaded, { event: 7, row: 1 })).toEqual({ row: null, near: null });
  });
});
