import { Menu } from "../kit/Menu";
import { Popup } from "../kit/Popup";
import { useDoc, useView } from "../session/session";
import { useRowActions, type Say } from "../shell/useRowActions";
import { shownBy, useExportTargets } from "./exportTargets";
import { entriesOf, shownEntry } from "./shownEntry";
import { useHidden } from "./useViewers";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

/** Where a pane's header was right-clicked: the pane's viewer and its item, and the pointer. */
export type PaneMenuAt = { viewer: number; item: string; x: number; y: number };

/** What a header action acts on, as it was when the action started: the pane, its item and the entry it shows. */
export type PaneTarget = { viewer: number; item: string; entry: string | null };

/**
 * The menu of a pane's header: the actions of the row of what the pane shows, without Edit (editing stays in the
 * sidebar), run by the same rules and handlers as the sidebar's row menu. Export video is this pane's own, whatever else
 * shows the item. A pane whose item has no such row has no menu. Render it once for the area: the
 * dialogs Information opens outlive the menu.
 */
export function PaneMenu({
  at,
  onClose,
  onAction,
}: {
  at: PaneMenuAt | null;
  onClose: () => void;
  /** Called as an action starts, with what it acts on; the answer is told how the action ends. */
  onAction: (target: PaneTarget) => Say;
}) {
  const actions = useRowActions();
  // The menu follows the viewer's export as it becomes ready or leaves.
  useExportTargets();
  const [viewers] = useView("viewers");
  const hidden = useHidden();
  const item = useDoc()?.workspace.items.find(({ id }) => id === at?.item);
  const content = item?.resolution.status === "Resolved" ? item.resolution.content : undefined;
  // What the pane shows, whether or not its playback is ready: the entry the viewer shows (`shownEntry`, as the
  // playlist viewer and the backend choose it), else, for a live stream, the item's own row.
  const entry = content && shownEntry(entriesOf(content), hidden, viewers[at?.viewer ?? -1]?.entry);
  const row =
    at &&
    actions.tree.find(
      ({ item: id, info }) =>
        id === at.item &&
        (info.kind === "item" || info.kind === "entry") &&
        (entry ? info.entry === entry.id : info.kind === "item"),
    );
  // Export is the pane's own viewer's, once it has one for this row.
  const shown = at ? shownBy(at.viewer) : undefined;
  const exporter = shown && shown.entry === row?.info.entry ? shown.run : undefined;
  return (
    <>
      {actions.dialogs}
      {at && row && (
        <Popup at={at} onClose={onClose} className="z-20 min-w-max">
          <Menu
            entries={actions.menuOf(row, exporter, false)}
            checked={NOTHING_CHECKED}
            onSelect={(choice) => {
              onClose();
              const target = { viewer: at.viewer, item: at.item, entry: entry?.id ?? null };
              actions.choose(row, choice.id, exporter, onAction(target));
            }}
          />
        </Popup>
      )}
    </>
  );
}
