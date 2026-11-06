import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { LiveConfirmation } from "../generated/api";
import { Button } from "../kit/Button";
import { Menu, type MenuCommand, type MenuEntry } from "../kit/Menu";
import { Popup } from "../kit/Popup";
import { SectionCaption } from "../kit/SectionCaption";
import { ACTIONS, type Row, type RowAction, type SectionView, type Shown } from "../live/rows";
import { HoverPicture } from "./HoverPicture";
import { useKept } from "./keptState";
import { LiveList, type Listed } from "./LiveList";
import { useHoverPicture } from "./useHoverPicture";

type SectionId = SectionView["section"]["id"];

const NOTHING_CHECKED: ReadonlySet<string> = new Set();
const NO_FOLDED: ReadonlySet<SectionId> = new Set();
const NO_COLLAPSED: ReadonlySet<string> = new Set();
const NO_TRACK: { opened: ReadonlySet<SectionId>; revealed: ReadonlySet<SectionId> } = {
  opened: new Set(),
  revealed: new Set(),
};

/** The menu of a row: its actions in order, with a line between the groups of them. */
const menuEntries = (row: Row, mayAct: boolean): MenuEntry[] =>
  row.menu.flatMap((action, at): MenuEntry[] => {
    const look = ACTIONS[action];
    const previous = at > 0 ? ACTIONS[row.menu[at - 1] as RowAction] : look;
    return [
      ...(previous.group !== look.group ? [{ kind: "separator" } as const] : []),
      {
        kind: "action",
        id: action,
        label: look.menu,
        tip: look.tip,
        // Looking is for everyone; the rest changes the workspace.
        disabled: action !== "information" && !mayAct,
      },
    ];
  });

const holdsActive = (rows: readonly Row[]): boolean =>
  rows.some((row) => row.active || holdsActive(row.children));

interface Props {
  views: readonly SectionView[];
  /** Whether the search is on, which unfolds what it finds. */
  searching: boolean;
  mayAct: boolean;
  mayConfirm: boolean;
  onAddStream: () => void;
  /** Enter or a double click on a row: start its stream, or bring its viewer to the front. */
  onOpen: (row: Row) => void;
  onRemove: (row: Row) => void;
  onAct: (row: Row, action: RowAction) => void;
  onConfirm: (row: Row, confirmation: LiveConfirmation) => void;
}

/**
 * The Live view's sections: a caption each (a click on it folds it) over its rows. A click on a row opens its info
 * lines, closing the one open before; a row with streams under it folds with its arrow; Enter and a double click open
 * it, and Delete removes it (see `LiveList` for the keys). A right click or the menu key opens the row's menu, and
 * the focus returns to the tree when the menu goes. A section opens again for a search that finds something in it,
 * and for a stream that turns open until the stream closes or the user folds it.
 */
export function LiveTree({
  views,
  searching,
  mayAct,
  mayConfirm,
  onAddStream,
  onOpen,
  onRemove,
  onAct,
  onConfirm,
}: Props) {
  const prefix = useId();
  const [folded, setFolded] = useKept("live:folded", NO_FOLDED);
  const [collapsed, setCollapsed] = useKept("live:collapsed", NO_COLLAPSED);
  const [info, setInfo] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ row: Row; x: number; y: number } | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  // The tree a menu came from, which takes the focus back.
  const menuFrom = useRef<HTMLElement | null>(null);

  // The snapshot pictures on offer: a hover ends once its row is gone.
  const pictures = useMemo(
    () =>
      new Set(
        views.flatMap(({ shown }) =>
          shown.flatMap(({ row, children }) =>
            [row, ...children.map((child) => child.row)].flatMap((own) => own.thumbnail ?? []),
          ),
        ),
      ),
    [views],
  );
  const picture = useHoverPicture(pictures);

  // The sections that hold a stream open now, and those a stream newly opened in has unfolded.
  const opened = new Set(
    views.filter(({ section }) => holdsActive(section.rows)).map(({ section }) => section.id),
  );
  const [track, setTrack] = useKept("live:track", NO_TRACK);
  useEffect(() => {
    if (opened.size === track.opened.size && [...opened].every((id) => track.opened.has(id)))
      return;
    setTrack({
      opened,
      revealed: new Set([
        ...[...track.revealed].filter((id) => opened.has(id)),
        ...[...opened].filter((id) => !track.opened.has(id)),
      ]),
    });
  });

  const toggle = <T,>(set: ReadonlySet<T>, value: T): Set<T> => {
    const next = new Set(set);
    if (!next.delete(value)) next.add(value);
    return next;
  };
  const fold = (id: SectionId) => {
    setFolded((now) => toggle(now, id));
    setTrack((now) => ({
      ...now,
      revealed: new Set([...now.revealed].filter((other) => other !== id)),
    }));
  };
  const closeMenu = useCallback(() => {
    setMenu(null);
    menuFrom.current?.focus({ preventScroll: true });
  }, []);

  /** The rows of a section the keyboard walks: the shown ones, with those under an unfolded row. */
  const listed = (shown: readonly Shown[]): Listed[] =>
    shown.flatMap(({ row, children }, at) => {
      const expanded = children.length > 0 ? searching || !collapsed.has(row.id) : null;
      return [
        {
          row,
          level: 1 as const,
          parent: null,
          expanded,
          position: at + 1,
          siblings: shown.length,
        },
        ...(expanded
          ? children.map((child, index) => ({
              row: child.row,
              level: 2 as const,
              parent: row.id,
              expanded: null,
              position: index + 1,
              siblings: children.length,
            }))
          : []),
      ];
    });
  const toggleInfo = (row: Row) =>
    setInfo((now) => (row.info.length === 0 ? now : now === row.id ? null : row.id));

  return (
    <div
      ref={scroller}
      className="flex min-h-list-least flex-1 flex-col overflow-y-auto [@media(max-height:20rem)]:min-h-row"
    >
      {views
        .filter(({ listed: shownSection }) => shownSection)
        .map(({ section, shown, filteredOut, searched }) => {
          const rows = listed(shown);
          const unfolded = searched || !folded.has(section.id) || track.revealed.has(section.id);
          return (
            <section key={section.id} aria-label={section.title}>
              <SectionCaption
                heading={section.title}
                count={section.rows.length}
                note={filteredOut ? `${filteredOut} filtered out` : undefined}
                mark={section.lit ? "live" : "muted"}
                fold={
                  section.rows.length > 0
                    ? { open: unfolded, onToggle: () => fold(section.id) }
                    : undefined
                }
                action={
                  section.adds && (
                    <Button
                      size="small"
                      icon="plus"
                      title="Add a camera and start its stream"
                      data-cursor-id="live:add"
                      disabled={!mayAct}
                      onClick={onAddStream}
                    >
                      Add stream
                    </Button>
                  )
                }
              />
              {section.rows.length === 0 && section.empty && (
                <p className="m-0 ml-l px-m pb-s text-small text-muted">{section.empty}</p>
              )}
              {unfolded && rows.length > 0 && (
                <LiveList
                  label={section.title}
                  prefix={prefix}
                  rows={rows}
                  scroller={scroller}
                  info={info}
                  mayAct={mayAct}
                  mayConfirm={mayConfirm}
                  onInfo={toggleInfo}
                  onOpen={onOpen}
                  onRemove={onRemove}
                  onFold={(row) => !searching && setCollapsed((now) => toggle(now, row.id))}
                  onMenu={(row, x, y, tree) => {
                    menuFrom.current = tree;
                    picture.leave();
                    setMenu({ row, x, y });
                  }}
                  onHover={(row, at) =>
                    at ? picture.enter(row.thumbnail, at.x, at.y) : picture.leave()
                  }
                  onAct={onAct}
                  onConfirm={onConfirm}
                />
              )}
            </section>
          );
        })}
      {picture.shown && <HoverPicture {...picture.shown} />}
      {menu && menu.row.menu.length > 0 && (
        <Popup at={menu} onClose={closeMenu} className="z-20 min-w-max">
          <Menu
            entries={menuEntries(menu.row, mayAct)}
            checked={NOTHING_CHECKED}
            onSelect={(choice: MenuCommand) => {
              const { row } = menu;
              closeMenu();
              onAct(row, choice.id as RowAction);
            }}
          />
        </Popup>
      )}
    </div>
  );
}
