import type { ViewerView } from "../generated/api";
import { SECTION_OF, type SectionId, type TreeRow } from "./treeModel";

/** What a view keeps of its folds: those the user made, those a viewer asked for, and what it last saw a viewer show. */
export type Folds = {
  /** The rows the user (or a viewer that moved on) closed. */
  collapsed: ReadonlySet<string>;
  /** The sections held unfolded by a viewer's row, until that viewer moves on or the user folds the section. */
  revealed: ReadonlySet<SectionId>;
  /** The rows the hosted viewers showed when last looked at, and the sections of their items. */
  shown: { rows: ReadonlySet<string>; sections: ReadonlySet<SectionId> };
  /** The rows with children already seen, which got their first fold then and have only the user's since. */
  known: ReadonlySet<string>;
};

/** The most entries a playlist can have and still start open; a longer one starts closed like any row with children. */
const OPEN_AT_FIRST_LIMIT = 200;

/** Folds before anything was shown. */
export const NO_FOLDS: Folds = {
  collapsed: new Set(),
  revealed: new Set(),
  shown: { rows: new Set(), sections: new Set() },
  known: new Set(),
};

/** `collapsed` with the rows of `tree` not in `known` that start closed, and the rows with children now known. */
function firstFolds(
  tree: readonly TreeRow[],
  collapsed: ReadonlySet<string>,
  known: ReadonlySet<string>,
): { collapsed: ReadonlySet<string>; known: ReadonlySet<string> } {
  const entries = new Map<string, number>();
  for (const { parent, info } of tree)
    if (parent !== null && info.kind === "entry")
      entries.set(parent, (entries.get(parent) ?? 0) + 1);
  const parents = tree.filter(({ hasChildren }) => hasChildren);
  const added = parents.filter(
    ({ info, playlist }) =>
      !known.has(info.key) && !(playlist && (entries.get(info.key) ?? 0) <= OPEN_AT_FIRST_LIMIT),
  );
  // A row that loses its children while its item resolves again is still known; one whose item left is not.
  const items = new Set(tree.map(({ item }) => item));
  const now = new Set([
    ...parents.map(({ info }) => info.key),
    ...[...known].filter((key) => items.has(itemOf(key))),
  ]);
  return {
    collapsed:
      added.length === 0
        ? collapsed
        : new Set([...collapsed, ...added.map(({ info }) => info.key)]),
    known: same(known, now) ? known : now,
  };
}

/** The item a row key belongs to: keys start with the item's id, then `/` and names, or the lane separator. */
const itemOf = (key: string) => key.split(/[/\u001f]/, 1)[0];

/**
 * `collapsed` without `key` and every row above it, so the row is drawn; the same set when it already is. The rows
 * above it in `kept` keep their fold.
 */
export function unfold(
  collapsed: ReadonlySet<string>,
  tree: readonly TreeRow[],
  key: string,
  kept: ReadonlySet<string> = new Set(),
): ReadonlySet<string> {
  const rows = new Map(tree.map((row) => [row.info.key, row]));
  const next = new Set(collapsed);
  for (let row = rows.get(key); row; row = row.parent === null ? undefined : rows.get(row.parent))
    if (!kept.has(row.info.key)) next.delete(row.info.key);
  return next.size === collapsed.size ? collapsed : next;
}

/**
 * Folds after the tree or the hosted `viewers` changed. A row with children seen for the first time starts closed,
 * except a playlist of at most `OPEN_AT_FIRST_LIMIT` entries. A row a viewer came to show (its item's top-level rows,
 * and the playlist entry it shows) is revealed with the rows above it that were not shown already, and its section
 * unfolds while a viewer shows something in it; a row or section the last viewer left closes. What stays shown keeps
 * the fold the user gave it. The same `folds` come back when nothing changed.
 */
export function revealShown(
  tree: readonly TreeRow[],
  viewers: readonly Pick<ViewerView, "item" | "entry">[],
  folds: Folds,
): Folds {
  const items = new Set(viewers.map(({ item }) => item));
  const entries = new Set(viewers.flatMap(({ entry }) => entry ?? []));
  const top = tree.filter(({ level, item }) => level === 1 && items.has(item));
  const entered = tree.filter(({ info }) => info.kind === "entry" && entries.has(info.entry ?? ""));
  const { shown } = folds;
  // Rows of an item a viewer still shows that are gone while the item has no content (resolving, or failed) are still
  // shown, not new when they return; once it resolves, the rows it lacks are gone for good.
  const present = new Set(tree.map(({ info }) => info.key));
  const unresolved = new Set(
    tree
      .filter(({ info }) => info.kind === "item" && info.status.status !== "Ready")
      .map(({ item }) => item),
  );
  const held = [...shown.rows].filter(
    (key) => !present.has(key) && items.has(itemOf(key)) && unresolved.has(itemOf(key)),
  );
  const now = {
    rows: new Set([...top, ...entered].map(({ info }) => info.key).concat(held)),
    sections: new Set(top.map(({ itemKind }) => SECTION_OF[itemKind])),
  };
  const first = firstFolds(tree, folds.collapsed, folds.known);
  let collapsed = first.collapsed;
  for (const key of now.rows)
    if (!shown.rows.has(key)) collapsed = unfold(collapsed, tree, key, shown.rows);
  const left = tree.filter(
    ({ info, hasChildren }) => hasChildren && shown.rows.has(info.key) && !now.rows.has(info.key),
  );
  if (left.length > 0) collapsed = new Set([...collapsed, ...left.map(({ info }) => info.key)]);
  const turned = [...now.sections].filter((id) => !shown.sections.has(id));
  const stayed = [...folds.revealed].filter((id) => now.sections.has(id));
  const revealed = new Set<SectionId>([...stayed, ...turned]);
  if (
    same(collapsed, folds.collapsed) &&
    same(revealed, folds.revealed) &&
    same(now.rows, shown.rows) &&
    same(now.sections, shown.sections) &&
    first.known === folds.known
  )
    return folds;
  return { collapsed, revealed, shown: now, known: first.known };
}

const same = <T>(a: ReadonlySet<T>, b: ReadonlySet<T>) =>
  a.size === b.size && [...a].every((x) => b.has(x));
