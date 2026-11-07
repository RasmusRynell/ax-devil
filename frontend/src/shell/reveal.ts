import { SECTION_OF, type SectionId, type TreeRow } from "./treeModel";

/** What a view keeps of its folds: those the user made, those a viewer asked for, and what it last saw a viewer show. */
export type Folds = {
  /** The rows the user (or a viewer that moved on) closed. */
  collapsed: ReadonlySet<string>;
  /** The sections held unfolded by a viewer's row, until that viewer moves on or the user folds the section. */
  revealed: ReadonlySet<SectionId>;
  /** The top-level rows the viewer showed when last looked at, and the sections they are in. */
  shown: { rows: ReadonlySet<string>; sections: ReadonlySet<SectionId> };
};

/** Folds before anything was shown. */
export const NO_FOLDS: Folds = {
  collapsed: new Set(),
  revealed: new Set(),
  shown: { rows: new Set(), sections: new Set() },
};

/** `collapsed` without `key` and every row above it, so the row is drawn; the same set when it already is. */
export function unfold(
  collapsed: ReadonlySet<string>,
  tree: readonly TreeRow[],
  key: string,
): ReadonlySet<string> {
  const rows = new Map(tree.map((row) => [row.info.key, row]));
  const next = new Set(collapsed);
  for (let row = rows.get(key); row; row = row.parent === null ? undefined : rows.get(row.parent))
    next.delete(row.info.key);
  return next.size === collapsed.size ? collapsed : next;
}

/**
 * Folds after the viewer came to show `item` (or none): a row that turned open is revealed with the rows above it, and
 * its section unfolds until the viewer moves on; a row the viewer moved on from closes. A row that stays open keeps
 * the fold the user gave it. The same `folds` come back when nothing changed.
 */
export function revealShown(
  tree: readonly TreeRow[],
  item: string | undefined,
  folds: Folds,
): Folds {
  const rows = tree.filter(({ level, item: of }) => level === 1 && of === item);
  const now = {
    rows: new Set(rows.map(({ info }) => info.key)),
    sections: new Set(rows.map(({ itemKind }) => SECTION_OF[itemKind])),
  };
  const { shown } = folds;
  let collapsed = folds.collapsed;
  for (const key of now.rows) if (!shown.rows.has(key)) collapsed = unfold(collapsed, tree, key);
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
    same(now.sections, shown.sections)
  )
    return folds;
  return { collapsed, revealed, shown: now };
}

const same = <T>(a: ReadonlySet<T>, b: ReadonlySet<T>) =>
  a.size === b.size && [...a].every((x) => b.has(x));
