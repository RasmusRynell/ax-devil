/**
 * Which columns of a table are shown and how wide, from their measures, the room and the widths the user gave. Pure:
 * measuring is `measure.ts`, applying is `useColumns.ts`. Widths are CSS pixels and include the cell's padding.
 */

import type { Column } from "./columns";

/** What a column needs, measured over the rows in view. */
export interface Measure {
  /** Wide enough for its longest value. */
  natural: number;
  /** The narrowest before other columns are hidden. */
  floor: number;
  /** The narrowest it is ever shrunk to. */
  least: number;
  /** It shows nothing in any row in view. */
  empty: boolean;
}

/** The columns that are shown (indices into the columns) and the width of each, in the same order. */
export interface Fit {
  shown: number[];
  widths: number[];
}

type Fittable = Pick<Column<unknown>, "title" | "priority" | "stretch">;
type Widths = ReadonlyMap<string, number>;

const sessionWidths = new Map<string, Map<string, number>>();

/** The widths the user gave the columns of a table, by column title, for as long as the app runs; never saved. */
export function rememberedWidths(tableKey: string): Map<string, number> {
  let widths = sessionWidths.get(tableKey);
  if (!widths) sessionWidths.set(tableKey, (widths = new Map()));
  return widths;
}

const sum = (values: readonly number[]): number =>
  values.reduce((total, value) => total + value, 0);

/** What a column asks for: the width the user gave it, else its natural one. */
function sizes(cols: readonly Fittable[], measures: readonly Measure[], user: Widths) {
  const pref = (i: number): number => {
    const saved = user.get(cols[i].title);
    return saved === undefined ? measures[i].natural : Math.max(saved, measures[i].least);
  };
  return {
    pref,
    floorOf: (i: number) => Math.min(measures[i].floor, pref(i)),
    leastOf: (i: number) => Math.min(measures[i].least, pref(i)),
  };
}

/** `preferred` as it is, or all shrunk toward `floors` in proportion to what they can give up if they exceed `room`. */
function shrink(preferred: readonly number[], floors: readonly number[], room: number): number[] {
  const wanted = sum(preferred);
  if (wanted <= room) return [...preferred];
  const bottom = sum(floors);
  const slack = Math.max(0, room - bottom);
  return preferred.map(
    (width, k) =>
      floors[k] + Math.floor(((width - floors[k]) * slack) / Math.max(1, wanted - bottom)),
  );
}

/**
 * The columns that fit in `available` pixels. A column that shows nothing is hidden first, then the one with the
 * highest priority number (the later one on a tie), until the rest fit at their floors; columns with priority 0 and
 * stretch columns stay. A column the user sized keeps that width while there is room; the others shrink first. The
 * stretch column without a width of its own, else the last one, takes what is left.
 */
export function fitColumns(
  cols: readonly Fittable[],
  measures: readonly Measure[],
  available: number,
  user: Widths,
): Fit {
  const { pref, floorOf, leastOf } = sizes(cols, measures, user);
  let shown = cols.map((_, i) => i);
  const absorberOf = (list: readonly number[]): number =>
    list.find((i) => cols[i].stretch && !user.has(cols[i].title)) ?? list[list.length - 1];
  const hideable = () =>
    shown.length > 1 ? shown.filter((i) => (cols[i].priority ?? 0) > 0 && !cols[i].stretch) : [];
  const hide = (candidates: readonly number[]) => {
    const gone = candidates.reduce((a, b) =>
      (cols[b].priority ?? 0) >= (cols[a].priority ?? 0) ? b : a,
    );
    shown = shown.filter((i) => i !== gone);
  };
  const wanted = () => {
    const absorber = absorberOf(shown);
    return sum(shown.map((i) => (i === absorber ? floorOf(i) : pref(i))));
  };

  while (wanted() > available && hideable().some((i) => measures[i].empty))
    hide(hideable().filter((i) => measures[i].empty));
  while (sum(shown.map(floorOf)) > available && hideable().length > 0) hide(hideable());

  const absorber = absorberOf(shown);
  const floors = shown.map(floorOf);
  let widths: number[];
  if (sum(floors) > available) {
    // Nothing is left to hide.
    widths = shrink(floors, shown.map(leastOf), available);
  } else {
    const others = shown.filter((i) => i !== absorber);
    const own = others.filter((i) => user.has(cols[i].title));
    const auto = others.filter((i) => !own.includes(i));
    const spare = available - floorOf(absorber);
    const autoWidths = shrink(auto.map(pref), auto.map(floorOf), spare - sum(own.map(pref)));
    const ownWidths = shrink(own.map(pref), own.map(floorOf), spare - sum(autoWidths));
    const byColumn = new Map<number, number>([[absorber, floorOf(absorber)]]);
    auto.forEach((i, k) => byColumn.set(i, autoWidths[k]));
    own.forEach((i, k) => byColumn.set(i, ownWidths[k]));
    widths = shown.map((i) => byColumn.get(i) ?? 0);
  }
  widths[shown.indexOf(absorber)] += Math.max(0, available - sum(widths));
  return { shown, widths };
}

/**
 * The width to remember for column `index` dragged to `requested`: as much as the other `shown` columns leave room
 * for, and no less than its least. `null` when that changes nothing: it is where the drag started and the user had
 * not sized the column before.
 */
export function dragWidth(
  cols: readonly Fittable[],
  measures: readonly Measure[],
  user: Widths,
  shown: readonly number[],
  index: number,
  requested: number,
  available: number,
  start: { width: number; sized: boolean },
): number | null {
  const { floorOf } = sizes(cols, measures, user);
  const reserved = sum(shown.filter((i) => i !== index).map(floorOf));
  const applied = Math.max(measures[index].least, Math.min(requested, available - reserved));
  return applied === start.width && !start.sized ? null : applied;
}

/** The width that shows a column's longest value whole. */
export const fitWidth = (measure: Measure): number => measure.natural;
