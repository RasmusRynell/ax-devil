import { useMemo, useState, type ReactNode } from "react";
import { reasonOf } from "../errors";
import type { MenuEntry } from "../kit/Menu";
import { useStreamStatuses, type StreamStatus } from "../live/streamState";
import { useCan, useCommand, useDoc } from "../session/session";
import { useShow } from "../viewer/useViewers";
import { useItemDialogs } from "./ItemDialogs";
import { rowInformation } from "./rowInformation";
import { canEdit, isHiddenLane, rowMenu, type Powers } from "./rowMenu";
import { treeOf, type TreeRow } from "./treeModel";

/** What the actions on a row do, shared by the sidebar's tree and a viewer's header menu: one set of rules and handlers. */
export type RowActions = {
  /** The tree of the workspace's rows, with the state of each live stream. */
  tree: TreeRow[];
  statuses: ReadonlyMap<string, StreamStatus>;
  powers: Powers;
  /** Why the last action failed, until the next one succeeds. */
  problem: string | null;
  /** The dialogs Information and Edit open; render them where the actions are used. */
  dialogs: ReactNode;
  /** Opens the row, to the side with `side`; a row that cannot open says why in `problem`. */
  open: (row: TreeRow, side?: boolean) => Promise<void>;
  toggleHidden: (row: TreeRow) => void;
  editRow: (row: TreeRow) => void;
  removeRow: (row: TreeRow) => void;
  /** The row's menu; `exporter` is the export of the viewer that shows it, if any. */
  menuOf: (row: TreeRow, exporter: (() => void) | undefined, withEdit?: boolean) => MenuEntry[];
  /** Does what the chosen menu entry `id` says to `row`. */
  choose: (row: TreeRow, id: string, exporter: (() => void) | undefined) => void;
};

/**
 * The actions of a workspace row: open, Hide and Unhide, Information, Export video, Edit and Remove, and the menu that
 * offers them. Each decides by the same rules (`rowMenu`, `canEdit`, `isHiddenLane`), whichever surface asks.
 */
export function useRowActions(): RowActions {
  const workspace = useDoc()?.workspace;
  const command = useCommand();
  const show = useShow();
  const content = useCan("content");
  const host = useCan("host");
  const powers = useMemo(() => ({ content, host }), [content, host]);
  const { edit, inform, dialogs } = useItemDialogs();
  const statuses = useStreamStatuses();
  const tree = useMemo(() => (workspace ? treeOf(workspace, statuses) : []), [workspace, statuses]);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(error instanceof Error ? error.message : String(error));
  // A lane's row brings forward a viewer already showing its entry; `side` opens a pinned viewer to the right of the
  // front pane.
  const present = (row: TreeRow, side = false) =>
    show(row.item, row.info.kind === "item" ? undefined : (row.info.entry ?? undefined), {
      side,
      reuse: !side && row.info.kind === "lane",
    }).then(() => setProblem(null), fail);
  // A row that cannot open says why and sends nothing; one still resolving, or a lane hidden itself (Unhide and open
  // opens that), does nothing.
  const open = (row: TreeRow, side = false) => {
    const { status } = row.info;
    if (status.status === "Pending" || isHiddenLane(row)) return Promise.resolve();
    if (status.status === "Unavailable") {
      setProblem(`${row.info.label}: ${reasonOf(status)}`);
      return Promise.resolve();
    }
    return present(row, side);
  };
  const setHidden = (row: TreeRow, hidden: boolean) =>
    row.target
      ? command({ type: "content", command: { type: "setHidden", target: row.target, hidden } })
      : Promise.resolve();
  const toggleHidden = (row: TreeRow) =>
    void setHidden(row, !row.info.hidden).then(() => setProblem(null), fail);
  const unhideOpen = (row: TreeRow) => void setHidden(row, false).then(() => present(row), fail);
  const itemOf = (row: TreeRow) => workspace?.items.find(({ id }) => id === row.item);
  /** Edit and Remove act on the row's whole item and only a top-level row has them. */
  const editRow = (row: TreeRow) => {
    const item = itemOf(row);
    if (row.level === 1 && canEdit(item, powers)) edit(item);
  };
  const removeRow = (row: TreeRow) => {
    if (row.level !== 1 || !powers.content) return;
    command({ type: "content", command: { type: "remove", item: row.item } }).then(
      () => setProblem(null),
      fail,
    );
  };
  const choose = (row: TreeRow, id: string, exporter: (() => void) | undefined) => {
    if (id === "open" || id === "open-side") void open(row, id === "open-side");
    else if (id === "unhide-open") unhideOpen(row);
    else if (id === "export-video") exporter?.();
    else if (id === "information") {
      const info = rowInformation(row, workspace?.items ?? [], workspace?.rows ?? []);
      if (info) inform(info);
    } else if (id === "edit") editRow(row);
    else if (id === "remove") removeRow(row);
    else toggleHidden(row);
  };
  const menuOf = (row: TreeRow, exporter: (() => void) | undefined, withEdit = true) =>
    rowMenu(row, itemOf(row), powers, exporter !== undefined, withEdit);

  return {
    tree,
    statuses,
    powers,
    problem,
    dialogs,
    open,
    toggleHidden,
    editRow,
    removeRow,
    menuOf,
    choose,
  };
}
