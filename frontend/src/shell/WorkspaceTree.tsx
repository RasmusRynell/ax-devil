import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type { IconName } from "../kit/icons";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { Menu, type MenuCommand, type MenuEntry } from "../kit/Menu";
import { NoticeList, noticeOf } from "../kit/NoticeList";
import { Popover } from "../kit/Popover";
import { Popup } from "../kit/Popup";
import { SectionCaption } from "../kit/SectionCaption";
import { Switch } from "../kit/Switch";
import { useCan, useCommand, useDoc } from "../session/session";
import { useShow } from "../viewer/useViewers";
import { treeOf, visibleRows, type TreeRow } from "./treeModel";
import { useRowWindow } from "./useRowWindow";

const ROW_HEIGHT = "var(--spacing-row)";

/** The icon in front of a row. */
function iconOf({ info, playlist }: TreeRow): IconName {
  if (info.status.status === "Unavailable") return "triangle-alert";
  if (info.kind === "item") return playlist ? "list-video" : "square-play";
  return info.kind === "entry" ? "video" : "layers";
}

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

/** Why a row cannot be opened as it is or what resolving left out, for assistive technology and the tooltip. */
function noteOf({ info, dimmed }: TreeRow): string {
  const { status } = info.status;
  // A seat that may not read the host's failures is told the failure, not why.
  if (status === "Unavailable")
    return info.status.reason ? `unavailable: ${info.status.reason}` : "unavailable";
  if (status === "Pending") return "opening";
  const warned = info.warnings.map(({ problem }) => problem).join("; ");
  return [dimmed ? "hidden" : "", warned].filter(Boolean).join(", ");
}

/**
 * The workspace's items as a tree: each item, then its entries and their lanes, from the backend's rows. Click or
 * Enter opens the row in its viewer; the arrow keys move, expand and collapse; the row menu (right click,
 * Shift+F10 or the Menu key) opens it or hides and unhides it. Hidden rows show, dimmed, only with Show hidden.
 */
export function WorkspaceTree() {
  const workspace = useDoc()?.workspace;
  const command = useCommand();
  const show = useShow();
  const hides = useCan("content");
  const [showHidden, setShowHidden] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [current, setCurrent] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const prefix = useId();
  const lastAt = useRef(0);

  const tree = useMemo(() => (workspace ? treeOf(workspace) : []), [workspace]);
  const shown = useMemo(
    () => visibleRows(tree, collapsed, showHidden),
    [tree, collapsed, showHidden],
  );
  const { ref, start, end, update, reveal } = useRowWindow(shown.length);

  // A row that went away (hidden, say) leaves the position where it was, on the row that took its place.
  const found = shown.findIndex(({ row }) => row.info.key === current);
  const at = found >= 0 ? found : Math.min(lastAt.current, shown.length - 1);
  const active = shown[at]?.row;
  useEffect(() => {
    lastAt.current = Math.max(at, 0);
  });

  const go = (index: number) => {
    const next = Math.max(0, Math.min(shown.length - 1, index));
    const row = shown[next]?.row;
    if (!row) return;
    setCurrent(row.info.key);
    reveal(next);
  };
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
  const toggleHidden = (row: TreeRow) => {
    if (!row.target) return;
    command({
      type: "content",
      command: { type: "setHidden", target: row.target, hidden: !row.info.hidden },
    }).then(() => setProblem(null), fail);
  };
  const closeMenu = useCallback(() => {
    setMenu(null);
    ref.current?.focus({ preventScroll: true });
  }, [ref]);

  const menuAt = (row: TreeRow, x: number, y: number) => {
    setCurrent(row.info.key);
    setMenu({ key: row.info.key, x, y });
  };
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

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.altKey || event.metaKey || !active) return;
    const hasKids = active.hasChildren;
    const isCollapsed = collapsed.has(active.info.key);
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(shown.length - 1);
    else if (event.key === "ArrowRight" && hasKids && isCollapsed)
      setCollapsedState(active.info.key, false);
    else if (event.key === "ArrowRight" && hasKids) go(at + 1);
    else if (event.key === "ArrowLeft" && hasKids && !isCollapsed)
      setCollapsedState(active.info.key, true);
    else if (event.key === "ArrowLeft") {
      const parent = shown.findIndex(({ row }) => row.info.key === active.parent);
      if (parent >= 0) go(parent);
    } else if (event.key === "Enter" || event.key === " ") void open(active);
    else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      const box = document.getElementById(`${prefix}-${at}`)?.getBoundingClientRect();
      menuAt(active, box?.left ?? 0, box?.bottom ?? 0);
    } else return;
    event.preventDefault();
  };

  return (
    <section aria-label="Workspace items" className="flex min-h-0 flex-1 flex-col gap-xs">
      <div className="flex items-center gap-m pr-m">
        <div className="min-w-0 flex-1">
          <SectionCaption heading="Items" />
        </div>
        <Switch label="Show hidden" checked={showHidden} onChange={setShowHidden} />
      </div>
      {problem && (
        <p role="alert" className="m-0 px-m break-words">
          {problem}
        </p>
      )}
      {shown.length === 0 ? (
        <p className="m-0 px-m text-muted">No items</p>
      ) : (
        <div
          ref={ref}
          role="tree"
          aria-label="Workspace"
          aria-activedescendant={`${prefix}-${at}`}
          tabIndex={0}
          onKeyDown={onKeyDown}
          onScroll={update}
          className="group/tree min-h-0 flex-1 overflow-y-auto focus:outline-none"
        >
          <div
            style={{
              paddingTop: `calc(${ROW_HEIGHT} * ${start})`,
              paddingBottom: `calc(${ROW_HEIGHT} * ${shown.length - end})`,
            }}
          >
            {shown.slice(start, end).map(({ row, position, siblings }, offset) => {
              const index = start + offset;
              const { info } = row;
              const isCurrent = index === at;
              const note = noteOf(row);
              const folded = collapsed.has(info.key);
              return (
                <div
                  key={info.key}
                  id={`${prefix}-${index}`}
                  role="treeitem"
                  data-cursor-id={`tree:${info.key}`}
                  aria-level={row.level}
                  aria-posinset={position}
                  aria-setsize={siblings}
                  aria-expanded={row.hasChildren ? !folded : undefined}
                  aria-busy={info.status.status === "Pending" || undefined}
                  title={note || undefined}
                  onClick={() => {
                    setCurrent(info.key);
                    ref.current?.focus({ preventScroll: true });
                    void open(row);
                  }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    menuAt(row, event.clientX, event.clientY);
                  }}
                  style={{
                    paddingLeft: `calc(var(--spacing-m) + var(--spacing-xl) * ${row.level - 1})`,
                  }}
                  className={`group/row flex h-row cursor-pointer items-center gap-s rounded-control pr-s hover:bg-hover ${
                    row.dimmed || info.status.status !== "Ready" ? "text-muted" : "text-fg"
                  } ${isCurrent ? "bg-hover group-focus-visible/tree:outline-(length:--stroke-focus) group-focus-visible/tree:-outline-offset-(--stroke-focus) group-focus-visible/tree:outline-accent" : ""}`}
                >
                  {row.hasChildren ? (
                    <span
                      aria-hidden
                      onClick={(event) => {
                        event.stopPropagation();
                        setCollapsedState(info.key, !folded);
                      }}
                    >
                      <Icon name={folded ? "chevron-right" : "chevron-down"} />
                    </span>
                  ) : (
                    <span aria-hidden className="size-icon shrink-0" />
                  )}
                  <Icon name={iconOf(row)} />
                  <span className="min-w-0 flex-1 truncate">{info.label}</span>
                  {note && <span className="sr-only">{`, ${note}`}</span>}
                  {info.warnings.length > 0 && (
                    <span onClick={(event) => event.stopPropagation()}>
                      <Popover
                        label={`${info.label}: warnings`}
                        trigger={<Icon name="triangle-alert" />}
                        className="size-row justify-center"
                      >
                        <NoticeList notices={info.warnings.map(noticeOf)} />
                      </Popover>
                    </span>
                  )}
                  {row.target && (
                    <IconButton
                      icon={info.hidden ? "eye" : "eye-off"}
                      label={`${info.hidden ? "Unhide" : "Hide"} ${info.label}`}
                      tabIndex={-1}
                      disabled={!hides}
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleHidden(row);
                      }}
                      className={`rounded-control ${
                        info.hidden || isCurrent
                          ? ""
                          : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
                      }`}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
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
