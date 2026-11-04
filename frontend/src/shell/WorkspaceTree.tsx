import { useCallback, useId, useMemo, useRef, useState } from "react";
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
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const prefix = useId();
  const scroller = useRef<HTMLDivElement>(null);
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
    <section aria-label="Workspace items" className="flex min-h-0 flex-1 flex-col gap-xs">
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
              <section key={section.id} aria-label={section.title}>
                <SectionCaption
                  heading={section.title}
                  count={section.count}
                  note={out ? `${out} filtered out` : undefined}
                  mark={section.mark}
                  fold={{ open: unfolded, onToggle: () => toggleFolded(section.id) }}
                />
                {unfolded && shown.length > 0 && (
                  <WorkspaceList
                    label={section.title}
                    prefix={`${prefix}-${section.id}`}
                    rows={shown}
                    collapsed={collapsed}
                    scroller={scroller}
                    hides={hides}
                    onOpen={(row) => void open(row)}
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
