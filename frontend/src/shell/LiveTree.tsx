import { useId, useMemo, useRef, useState } from "react";
import { Button } from "../kit/Button";
import type { MenuEntry } from "../kit/Menu";
import { SectionCaption } from "../kit/SectionCaption";
import { ACTIONS, type Row, type RowAction, type SectionView } from "../live/rows";
import { useKept } from "../kit/keptState";
import { withKey } from "../kit/sets";
import { placeLive } from "./livePlaced";
import { useLiveTrack } from "./RevealShown";
import { TreeList } from "./tree/TreeList";
import { useTreeMenu } from "./tree/useTreeMenu";

type SectionId = SectionView["section"]["id"];

const NO_FOLDED: ReadonlySet<SectionId> = new Set();
const NO_COLLAPSED: ReadonlySet<string> = new Set();

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

interface Props {
  views: readonly SectionView[];
  /** Whether the search is on, which unfolds what it finds. */
  searching: boolean;
  mayAct: boolean;
  onAddStream: () => void;
  /** Enter or a double click on a row: start its stream, or bring its viewer to the front. */
  onOpen: (row: Row) => void;
  onRemove: (row: Row) => void;
  onAct: (row: Row, action: RowAction) => void;
}

/**
 * The Live view's sections: a caption each (a click on it folds it) over its rows. A click on a row opens its info
 * lines, closing the one open before. A section opens again for a search that finds something in it, and for a stream
 * that turns open until the stream closes or the user folds it.
 */
export function LiveTree({
  views,
  searching,
  mayAct,
  onAddStream,
  onOpen,
  onRemove,
  onAct,
}: Props) {
  const prefix = useId();
  const [folded, setFolded] = useKept("live:folded", NO_FOLDED);
  const [collapsed, setCollapsed] = useKept("live:collapsed", NO_COLLAPSED);
  const [info, setInfo] = useState<string | null>(null);
  const [current, setCurrent] = useState<Partial<Record<SectionId, string>>>({});
  const scroller = useRef<HTMLDivElement>(null);

  const sections = useMemo(
    () =>
      views
        .filter(({ listed }) => listed)
        .map((view) => ({
          view,
          rows: placeLive(view.shown, { collapsed, searching, info, mayAct }),
        })),
    [views, collapsed, searching, info, mayAct],
  );
  const menu = useTreeMenu(
    (id) => sections.flatMap(({ rows }) => rows).find(({ key }) => key === id)?.row,
    (row) => menuEntries(row, mayAct),
    (row, id) => onAct(row, id as RowAction),
  );

  // The sections a stream newly opened in has unfolded, which the window keeps up to date.
  const [track, setTrack] = useLiveTrack();

  const fold = (id: SectionId) => {
    setFolded((now) => withKey(now, id, !now.has(id)));
    setTrack((now) => ({
      ...now,
      revealed: new Set([...now.revealed].filter((other) => other !== id)),
    }));
  };
  const toggleInfo = (row: Row) =>
    setInfo((now) => (row.info.length === 0 ? now : now === row.id ? null : row.id));

  return (
    <div
      ref={scroller}
      className="flex min-h-list-least flex-1 flex-col overflow-y-auto [@media(max-height:20rem)]:min-h-row"
    >
      {sections.map(({ view: { section, filteredOut, searched }, rows }) => {
        const go = (to: number | "first" | "last") => {
          const index = to === "first" ? 0 : to === "last" ? rows.length - 1 : to;
          const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
          if (next) setCurrent((now) => ({ ...now, [section.id]: next.key }));
        };
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
              <TreeList
                variant="live"
                label={section.title}
                prefix={`${prefix}-${section.id}`}
                rows={rows}
                scroller={scroller}
                current={current[section.id] ?? null}
                chords={false}
                onCurrent={(key) => setCurrent((now) => ({ ...now, [section.id]: key }))}
                onMove={go}
                // A section's tree starts on its first row again when it is drawn again.
                onGone={() => setCurrent((now) => ({ ...now, [section.id]: undefined }))}
                onFold={({ key }, collapse) =>
                  !searching && setCollapsed((now) => withKey(now, key, collapse))
                }
                onCommand={({ row }, command) => {
                  if (command === "open") onOpen(row);
                  else if (command === "info") toggleInfo(row);
                  else if (command === "remove") onRemove(row);
                  else onAct(row, command);
                }}
                onButton={({ row }, id) => onAct(row, id as RowAction)}
                onMenu={({ key }, x, y, tree) => menu.open(key, x, y, tree)}
              />
            )}
          </section>
        );
      })}
      {menu.popup}
    </div>
  );
}
