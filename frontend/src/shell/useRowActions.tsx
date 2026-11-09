import { useMemo, useState, type ReactNode } from "react";
import { reasonOf } from "../errors";
import type { MenuEntry } from "../kit/Menu";
import { useStreamStatuses, type StreamStatus } from "../live/streamState";
import { useCan, useCommand, useDoc, useView } from "../session/session";
import { useShow } from "../viewer/useViewers";
import { useItemDialogs } from "./ItemDialogs";
import { rowInformation } from "./rowInformation";
import { canEdit, isHiddenLane, isStream, rowMenu, type Powers } from "./rowMenu";
import { openRows, treeOf, type TreeRow } from "./treeModel";

/** Told how an action ended: its failure message, or `null` when it worked. The sidebar's tree keeps it as `problem`. */
export type Say = (message: string | null) => void;

/** What the actions on a row do, shared by the sidebar's tree and a viewer's header menu: one set of rules and handlers. */
export type RowActions = {
  /** The tree of the workspace's rows, with the state of each live stream. */
  tree: TreeRow[];
  statuses: ReadonlyMap<string, StreamStatus>;
  /** The keys of the rows a viewer shows. */
  openKeys: ReadonlySet<string>;
  powers: Powers;
  /** Why the last action of the tree failed, until the next one succeeds. */
  problem: string | null;
  /** The dialogs Information and Edit open; render them where the actions are used. */
  dialogs: ReactNode;
  /** Opens the row, to the side with `side`; a row that cannot open says why through `say`. A live stream starts. */
  open: (row: TreeRow, side?: boolean, say?: Say) => Promise<void>;
  /** Closes the viewer that shows a live stream. */
  stop: (row: TreeRow, say?: Say) => void;
  toggleHidden: (row: TreeRow, say?: Say) => void;
  editRow: (row: TreeRow) => void;
  removeRow: (row: TreeRow, say?: Say) => void;
  /** The row's menu; `exporter` is the export of the viewer that shows it, if any. */
  menuOf: (row: TreeRow, exporter: (() => void) | undefined, withEdit?: boolean) => MenuEntry[];
  /** Does what the chosen menu entry `id` says to `row`, telling `say` how it ended (the tree's `problem` by default). */
  choose: (row: TreeRow, id: string, exporter: (() => void) | undefined, say?: Say) => void;
};

/**
 * The actions of a workspace row: open, Hide and Unhide, Information, Export video, Edit and Remove, and the menu that
 * offers them. Each decides by the same rules (`rowMenu`, `canEdit`, `isHiddenLane`), whichever surface asks. The tree
 * reads the one `problem` the hook keeps for it; a surface that owns its failure passes `say` with the action, and the
 * hook keeps nothing for it.
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
  const [views] = useView("viewers");
  const openKeys = useMemo(
    () =>
      openRows(
        tree,
        Object.values(views).flatMap((viewer) => viewer ?? []),
      ),
    [tree, views],
  );
  const [problem, setProblem] = useState<string | null>(null);
  const fail = (say: Say) => (error: unknown) =>
    say(error instanceof Error ? error.message : String(error));
  // A lane's row brings forward a viewer already showing its entry; `side` opens a pinned viewer to the right of the
  // front pane. A live stream starts beside the open viewers, never in place of one, and a running one comes forward.
  const present = (row: TreeRow, side: boolean, say: Say) =>
    show(row.item, row.info.kind === "item" ? undefined : (row.info.entry ?? undefined), {
      side: side || isStream(row),
      reuse: (!side && row.info.kind === "lane") || isStream(row),
      lane: row.info.kind === "lane" ? row.info.label : undefined,
    }).then(() => say(null), fail(say));
  const stop = (row: TreeRow, say: Say = setProblem) => {
    if (!powers.content) return;
    command({ type: "content", command: { type: "closeViewer", item: row.item } }).then(
      () => say(null),
      fail(say),
    );
  };
  // A row that cannot open says why and sends nothing; one still resolving, or a lane hidden itself (Unhide and open
  // opens that), does nothing.
  const open = (row: TreeRow, side = false, say: Say = setProblem) => {
    const { status } = row.info;
    if (status.status === "Pending" || isHiddenLane(row)) return Promise.resolve();
    if (status.status === "Unavailable") {
      say(`${row.info.label}: ${reasonOf(status)}`);
      return Promise.resolve();
    }
    return present(row, side, say);
  };
  const setHidden = (row: TreeRow, hidden: boolean) =>
    row.target
      ? command({ type: "content", command: { type: "setHidden", target: row.target, hidden } })
      : Promise.resolve();
  const toggleHidden = (row: TreeRow, say: Say = setProblem) =>
    void setHidden(row, !row.info.hidden).then(() => say(null), fail(say));
  const unhideOpen = (row: TreeRow, say: Say) =>
    void setHidden(row, false).then(() => present(row, false, say), fail(say));
  const itemOf = (row: TreeRow) => workspace?.items.find(({ id }) => id === row.item);
  /** Edit and Remove act on the row's whole item and only a top-level row has them. */
  const editRow = (row: TreeRow) => {
    const item = itemOf(row);
    if (row.level === 1 && canEdit(item, powers)) edit(item);
  };
  const removeRow = (row: TreeRow, say: Say = setProblem) => {
    if (row.level !== 1 || !powers.content) return;
    command({ type: "content", command: { type: "remove", item: row.item } }).then(
      () => say(null),
      fail(say),
    );
  };
  const choose = (
    row: TreeRow,
    id: string,
    exporter: (() => void) | undefined,
    say: Say = setProblem,
  ) => {
    if (id === "open" || id === "open-side") void open(row, id === "open-side", say);
    else if (id === "start") void open(row, false, say);
    else if (id === "stop") stop(row, say);
    else if (id === "unhide-open") unhideOpen(row, say);
    else if (id === "export-video") exporter?.();
    else if (id === "information") {
      const info = rowInformation(row, workspace?.items ?? [], workspace?.rows ?? []);
      if (info) inform(info);
    } else if (id === "edit") editRow(row);
    else if (id === "remove") removeRow(row, say);
    else toggleHidden(row, say);
  };
  const menuOf = (row: TreeRow, exporter: (() => void) | undefined, withEdit = true) =>
    rowMenu(row, itemOf(row), powers, exporter !== undefined, openKeys.has(row.info.key), withEdit);

  return {
    tree,
    statuses,
    openKeys,
    powers,
    problem,
    dialogs,
    open,
    stop,
    toggleHidden,
    editRow,
    removeRow,
    menuOf,
    choose,
  };
}
