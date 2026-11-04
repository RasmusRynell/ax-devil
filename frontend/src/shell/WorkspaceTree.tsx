import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Menu, type MenuCommand, type MenuEntry } from "../kit/Menu";
import { Popup } from "../kit/Popup";
import { SectionCaption } from "../kit/SectionCaption";
import { Switch } from "../kit/Switch";
import { useStreamStatuses } from "../live/streamState";
import { useCan, useCommand, useDoc } from "../session/session";
import { useShow } from "../viewer/useViewers";
import { HoverPicture } from "./HoverPicture";
import {
  filteredOut,
  sectionsOf,
  treeOf,
  visibleRows,
  type SectionId,
  type TreeRow,
} from "./treeModel";
import { useRowThumbnail } from "./useRowThumbnail";
import { WorkspaceList } from "./WorkspaceList";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

/**
 * The workspace's items as a tree in sections (Live, Videos, Playlists, Other; a click on a caption folds it): each
 * item, then its entries and their lanes, from the backend's rows. Click or Enter opens the row in its viewer; the
 * arrow keys move, expand and collapse; the row menu (right click, Shift+F10 or the Menu key) opens it or hides and
 * unhides it. Hidden rows show, dimmed, only with Show hidden, and a section says how many that leaves out.
 */
export function WorkspaceTree() {
  const workspace = useDoc()?.workspace;
  const command = useCommand();
  const show = useShow();
  const hides = useCan("content");
  const statuses = useStreamStatuses();
  const [showHidden, setShowHidden] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [folded, setFolded] = useState<ReadonlySet<SectionId>>(new Set());
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
  const sections = useMemo(
    () =>
      sectionsOf(tree, live).map((section) => ({
        section,
        shown: visibleRows(section.rows, collapsed, showHidden),
      })),
    [tree, live, collapsed, showHidden],
  );
  const thumbnail = useRowThumbnail(
    useMemo(() => sections.flatMap(({ shown }) => shown), [sections]),
  );

  // The rows of the unfolded sections in the order the keyboard walks them, and where each section's begin.
  const walk = useMemo(() => {
    const starts = new Map<SectionId, number>();
    const rows: { id: SectionId; key: string }[] = [];
    for (const { section, shown } of sections) {
      if (folded.has(section.id)) continue;
      starts.set(section.id, rows.length);
      for (const { row } of shown) rows.push({ id: section.id, key: row.info.key });
    }
    return { rows, starts };
  }, [sections, folded]);
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
    if (!next) return;
    // The focus first: a tree that takes it starts on its first row, which this move then overrides.
    inSection(next.id, '[role="tree"]')?.focus({ preventScroll: true });
    setCurrent(next.key);
  };
  const onLose = useCallback((id: SectionId) => {
    lost.current = id;
  }, []);
  // A tree that went away with the focus hands it to the row that took its place, else to its section's caption.
  useLayoutEffect(() => {
    const id = lost.current;
    if (id === null) return;
    lost.current = null;
    const next = walk.rows[at];
    if (next) {
      inSection(next.id, '[role="tree"]')?.focus({ preventScroll: true });
      setCurrent(next.key);
    } else
      (
        inSection(id, "button[aria-expanded]") ??
        root.current?.querySelector<HTMLElement>('[role="switch"]')
      )?.focus();
  });

  const fail = (error: unknown) =>
    setProblem(error instanceof Error ? error.message : String(error));
  const open = (row: TreeRow) =>
    show(row.item, row.info.kind === "item" ? undefined : (row.info.entry ?? undefined)).then(
      () => setProblem(null),
      fail,
    );
  const setCollapsedState = (key: string, collapse: boolean) =>
    setCollapsed((now) => {
      const next = new Set(now);
      if (collapse) next.add(key);
      else next.delete(key);
      return next;
    });
  const toggleFolded = (id: SectionId) =>
    setFolded((now) => {
      const next = new Set(now);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const toggleHidden = (row: TreeRow) => {
    if (!row.target) return;
    command({
      type: "content",
      command: { type: "setHidden", target: row.target, hidden: !row.info.hidden },
    }).then(() => setProblem(null), fail);
  };
  const closeMenu = useCallback(() => {
    setMenu(null);
    menuFrom.current?.focus({ preventScroll: true });
  }, []);

  const menuRow = menu && tree.find((row) => row.info.key === menu.key);
  const menuEntries: MenuEntry[] = menuRow
    ? [
        { kind: "action", id: "open", label: "Open" },
        ...(menuRow.target
          ? [
              {
                kind: "action" as const,
                id: "hidden",
                label: menuRow.info.hidden ? "Unhide" : "Hide",
                disabled: !hides,
              },
            ]
          : []),
      ]
    : [];
  const choose = (choice: MenuCommand) => {
    if (!menuRow) return;
    if (choice.id === "open") void open(menuRow);
    else toggleHidden(menuRow);
  };

  return (
    <section
      ref={root}
      aria-label="Workspace items"
      className="flex min-h-0 flex-1 flex-col gap-xs"
    >
      <div className="flex justify-end pr-m">
        <Switch label="Show hidden" checked={showHidden} onChange={setShowHidden} />
      </div>
      {problem && (
        <p role="alert" className="m-0 px-m break-words">
          {problem}
        </p>
      )}
      {sections.length === 0 ? (
        <p className="m-0 px-m text-muted">No items</p>
      ) : (
        <div ref={scroller} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {sections.map(({ section, shown }) => {
            const out = filteredOut(section.rows, showHidden);
            const unfolded = !folded.has(section.id);
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
                    collapsed={collapsed}
                    scroller={scroller}
                    hides={hides}
                    onOpen={(row) => void open(row)}
                    onCurrent={setCurrent}
                    onGo={go}
                    onLose={onLose}
                    onFold={setCollapsedState}
                    onHide={toggleHidden}
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
