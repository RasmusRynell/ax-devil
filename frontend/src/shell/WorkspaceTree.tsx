import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { SidebarView } from "../generated/api";
import { MenuButton } from "../kit/Button";
import { SearchField } from "../kit/Fields";
import { IconToggle } from "../kit/IconToggle";
import { Menu, type MenuCommand, type MenuEntry } from "../kit/Menu";
import { Popup } from "../kit/Popup";
import { SectionCaption } from "../kit/SectionCaption";
import { reasonOf } from "../errors";
import { useStreamStatuses } from "../live/streamState";
import { useCan, useCommand, useDoc } from "../session/session";
import { useShow } from "../viewer/useViewers";
import { HoverPicture } from "./HoverPicture";
import { useItemDialogs } from "./ItemDialogs";
import { rowInformation } from "./rowInformation";
import { useKept } from "./keptState";
import { useFolds } from "./RevealShown";
import { unfold } from "./reveal";
import { canEdit, rowMenu } from "./rowMenu";
import {
  filteredOut,
  ORDERS,
  sectionsOf,
  treeOf,
  visibleRows,
  type Order,
  type SectionId,
  type TreeRow,
} from "./treeModel";
import { useRowThumbnail } from "./useRowThumbnail";
import { WorkspaceList } from "./WorkspaceList";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();
const NO_CLOSED: ReadonlySet<string> = new Set();
const NO_FOLDED: ReadonlySet<SectionId> = new Set();
/** `now` with `key` added or removed. */
function withKey<T>(now: ReadonlySet<T>, key: T, on: boolean): Set<T> {
  const next = new Set(now);
  if (on) next.add(key);
  else next.delete(key);
  return next;
}
const ORDER_IDS: ReadonlySet<string> = new Set(ORDERS.map(([id]) => id));
const ORDER_ENTRIES: MenuEntry[] = ORDERS.map(([id, label]) => ({ kind: "check", id, label }));

/**
 * The workspace's items as a tree in the `listed` sections (Live, Videos, Playlists, Other; a click on a caption folds
 * it): each item, then its entries and their lanes, from the backend's rows. A click selects a row; a double click or
 * Enter opens it in its viewer; the arrow keys move, expand and collapse; the row menu (right click, Shift+F10 or the
 * Menu key) opens it or hides and unhides it. The rows of the item the front viewer shows are revealed, and a row the
 * viewer moves on from closes. Search, Sort, Show hidden and folds are kept per `view` by the window. Above the tree,
 * Search (`search` is its placeholder), Sort and Show hidden items: hidden rows
 * show, dimmed, only with Show hidden, and a section says how many that and the search leave out. While searching,
 * every row with a match inside is expanded, and clearing the search brings the user's folds back. `empty` is said
 * when the workspace has nothing to list.
 */
export function WorkspaceTree({
  view,
  listed,
  search,
  empty,
}: {
  view: SidebarView;
  listed: readonly SectionId[];
  search: string;
  empty: string;
}) {
  const workspace = useDoc()?.workspace;
  const command = useCommand();
  const show = useShow();
  const hides = useCan("content");
  const host = useCan("host");
  const powers = { content: hides, host };
  const { edit, inform, dialogs } = useItemDialogs();
  const statuses = useStreamStatuses();
  // What the user set up in this view stays while the window shows another view or hides the sidebar.
  const [showHidden, setShowHidden] = useKept(`${view}:showHidden`, false);
  const [query, setQuery] = useKept(`${view}:query`, "");
  const [order, setOrder] = useKept<Order>(`${view}:order`, "workspace");
  const [held, setHeld] = useFolds(view);
  const { collapsed } = held;
  // What the user closed since the search text last changed; the search ignores the saved folds above.
  const [closed, setClosed] = useKept(`${view}:closed`, NO_CLOSED);
  const [folded, setFolded] = useKept(`${view}:folded`, NO_FOLDED);
  const [current, setCurrent] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const prefix = useId();
  const root = useRef<HTMLElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const lastAt = useRef(0);
  // The section whose tree went away with the focus in it, until the focus is placed again.
  const lost = useRef<SectionId | null>(null);
  // The tree a menu came from, which takes the focus back.
  const menuFrom = useRef<HTMLElement | null>(null);

  const tree = useMemo(() => (workspace ? treeOf(workspace) : []), [workspace]);
  const live = useMemo(
    () => new Set([...statuses].flatMap(([item, { anyLive }]) => (anyLive ? [item] : []))),
    [statuses],
  );
  const searching = query.trim() !== "";
  const folds = searching ? closed : collapsed;
  const sections = useMemo(
    () =>
      sectionsOf(tree, live, listed, order).map((section) => {
        const shown = visibleRows(section.rows, folds, showHidden, query);
        return {
          section,
          shown,
          out: filteredOut(section.rows, showHidden, query),
          unfolded: searching
            ? shown.length > 0 && !closed.has(`section:${section.id}`)
            : !folded.has(section.id) || held.revealed.has(section.id),
        };
      }),
    [tree, live, listed, order, folds, showHidden, query, folded, held.revealed, closed, searching],
  );
  const noMatches =
    searching && sections.length > 0 && sections.every(({ shown, out }) => !shown.length && !out);
  const thumbnail = useRowThumbnail(
    useMemo(() => sections.flatMap(({ shown }) => shown), [sections]),
  );

  // The rows of the unfolded sections in the order the keyboard walks them, and where each section's begin.
  const walk = useMemo(() => {
    const starts = new Map<SectionId, number>();
    const rows: { id: SectionId; key: string }[] = [];
    for (const { section, shown, unfolded } of sections) {
      if (!unfolded) continue;
      starts.set(section.id, rows.length);
      for (const { row } of shown) rows.push({ id: section.id, key: row.info.key });
    }
    return { rows, starts };
  }, [sections]);
  // A row that went away (hidden, say) leaves the position where it was, on the row that took its place.
  const found = walk.rows.findIndex(({ key }) => key === current);
  const at =
    current === null ? -1 : found >= 0 ? found : Math.min(lastAt.current, walk.rows.length - 1);
  const onRow = walk.rows[at]?.key ?? null;
  useEffect(() => {
    lastAt.current = Math.max(at, 0);
  });

  const inSection = (id: SectionId, selector: string) =>
    root.current?.querySelector<HTMLElement>(`[data-section="${id}"] ${selector}`);
  const go = (index: number) => {
    const next = walk.rows[Math.max(0, Math.min(walk.rows.length - 1, index))];
    if (next) setCurrent(next.key);
  };
  const onLose = useCallback((id: SectionId) => {
    lost.current = id;
  }, []);
  // The focus follows the keyboard's row: if it was on one of the trees (or left with one that went away), it goes to
  // the tree of the row the keyboard is on, else to the caption of the section that lost its rows.
  useLayoutEffect(() => {
    const gone = lost.current;
    lost.current = null;
    const on = document.activeElement;
    // Only a tree itself follows; a control inside one, such as a warnings button, keeps the focus it took.
    if (gone === null && !(on?.getAttribute("role") === "tree" && root.current?.contains(on)))
      return;
    const next = walk.rows[at];
    const tree = next && inSection(next.id, '[role="tree"]');
    if (next && tree) {
      if (!tree.contains(document.activeElement)) tree.focus({ preventScroll: true });
      if (next.key !== current) setCurrent(next.key);
    } else if (gone !== null)
      (
        inSection(gone, "button[aria-expanded]") ??
        root.current?.querySelector<HTMLElement>('input[type="search"]')
      )?.focus();
  });

  const fail = (error: unknown) =>
    setProblem(error instanceof Error ? error.message : String(error));
  // A row that cannot open says why and sends nothing; one still resolving does nothing.
  const open = (row: TreeRow) => {
    const { status } = row.info;
    if (status.status === "Pending") return Promise.resolve();
    if (status.status === "Unavailable") {
      setProblem(`${row.info.label}: ${reasonOf(status)}`);
      return Promise.resolve();
    }
    return show(
      row.item,
      row.info.kind === "item" ? undefined : (row.info.entry ?? undefined),
    ).then(() => setProblem(null), fail);
  };
  // The search opens every row and section with a match; folding meanwhile lasts until the search text changes.
  const setCollapsedState = (key: string, collapse: boolean) =>
    searching
      ? setClosed((now) => withKey(now, key, collapse))
      : setHeld((now) => ({ ...now, collapsed: withKey(now.collapsed, key, collapse) }));
  // A double click shows the row and the rows above it before opening it.
  const reveal = (row: TreeRow) =>
    searching
      ? setClosed((now) => unfold(now, tree, row.info.key))
      : setHeld((now) => ({ ...now, collapsed: unfold(now.collapsed, tree, row.info.key) }));
  // A section a viewer's row holds open folds when the user says so, and then stays folded after the viewer moves on.
  const toggleFolded = (id: SectionId) => {
    if (searching) setClosed((now) => withKey(now, `section:${id}`, !now.has(`section:${id}`)));
    else if (folded.has(id) && !held.revealed.has(id)) setFolded((now) => withKey(now, id, false));
    else {
      setFolded((now) => withKey(now, id, true));
      setHeld((now) => ({ ...now, revealed: withKey(now.revealed, id, false) }));
    }
  };
  const toggleHidden = (row: TreeRow) => {
    if (!row.target) return;
    command({
      type: "content",
      command: { type: "setHidden", target: row.target, hidden: !row.info.hidden },
    }).then(() => setProblem(null), fail);
  };
  const itemOf = (row: TreeRow) => workspace?.items.find(({ id }) => id === row.item);
  /** Edit and Remove act on the row's whole item and only a top-level row has them. */
  const editRow = (row: TreeRow) => {
    const item = itemOf(row);
    if (row.level === 1 && canEdit(item, powers)) edit(item);
  };
  const removeRow = (row: TreeRow) => {
    if (row.level !== 1 || !hides) return;
    command({ type: "content", command: { type: "remove", item: row.item } }).then(
      () => setProblem(null),
      fail,
    );
  };
  const closeMenu = useCallback(() => {
    setMenu(null);
    menuFrom.current?.focus({ preventScroll: true });
  }, []);

  const menuRow = menu && tree.find((row) => row.info.key === menu.key);
  const menuEntries = menuRow ? rowMenu(menuRow, itemOf(menuRow), powers) : [];
  const choose = (choice: MenuCommand) => {
    if (!menuRow) return;
    if (choice.id === "open") void open(menuRow);
    else if (choice.id === "information") {
      const info = rowInformation(menuRow, workspace?.items ?? []);
      if (info) inform(info);
    } else if (choice.id === "edit") editRow(menuRow);
    else if (choice.id === "remove") removeRow(menuRow);
    else toggleHidden(menuRow);
  };

  return (
    <section
      ref={root}
      aria-label="Workspace items"
      className="flex min-h-0 flex-1 flex-col gap-xs"
    >
      <div className="flex shrink-0 flex-wrap items-center gap-s px-s pt-s">
        <SearchField
          placeholder={search}
          query={{
            value: query,
            onChange: (text) => {
              setQuery(text);
              setClosed(NO_CLOSED);
            },
          }}
          className="min-w-search-least flex-1"
        />
        <MenuButton
          label="Sort"
          alignEnd
          title="The order of the rows"
          entries={ORDER_ENTRIES}
          checked={new Set([order])}
          onSelect={({ id }: MenuCommand) => ORDER_IDS.has(id) && setOrder(id as Order)}
        />
        <IconToggle
          icon="eye"
          label="Show hidden items"
          pressed={showHidden}
          onChange={setShowHidden}
        />
      </div>
      {noMatches && <p className="m-0 px-m text-small text-muted">No matches</p>}
      {problem && (
        <p role="alert" className="m-0 px-m break-words">
          {problem}
        </p>
      )}
      {sections.length === 0 ? (
        <p className="m-0 px-m text-muted">{empty}</p>
      ) : (
        <div ref={scroller} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {sections.map(({ section, shown, out, unfolded }) => {
            if (shown.length === 0 && out === 0) return null;
            return (
              <section key={section.id} data-section={section.id} aria-label={section.title}>
                <SectionCaption
                  heading={section.title}
                  count={section.count}
                  note={out ? `${out} filtered out` : undefined}
                  mark={section.mark}
                  fold={{ open: unfolded, onToggle: () => toggleFolded(section.id) }}
                />
                {unfolded && shown.length > 0 && (
                  <WorkspaceList
                    id={section.id}
                    label={section.title}
                    prefix={`${prefix}-${section.id}`}
                    rows={shown}
                    offset={walk.starts.get(section.id) ?? 0}
                    current={onRow}
                    collapsed={folds}
                    scroller={scroller}
                    hides={hides}
                    onOpen={(row) => void open(row)}
                    onReveal={reveal}
                    onCurrent={setCurrent}
                    onGo={go}
                    onLose={onLose}
                    onFold={setCollapsedState}
                    onHide={toggleHidden}
                    onEdit={editRow}
                    onRemove={removeRow}
                    onMenu={(row, x, y, from) => {
                      menuFrom.current = from;
                      setMenu({ key: row.info.key, x, y });
                    }}
                    thumbnail={thumbnail}
                  />
                )}
              </section>
            );
          })}
        </div>
      )}
      {thumbnail.shown && <HoverPicture {...thumbnail.shown} />}
      {dialogs}
      {menu && (
        <Popup at={menu} onClose={closeMenu} className="z-20 min-w-max">
          <Menu
            entries={menuEntries}
            checked={NOTHING_CHECKED}
            onSelect={(choice) => {
              closeMenu();
              choose(choice);
            }}
          />
        </Popup>
      )}
    </section>
  );
}
