import { useMemo, useRef, useState } from "react";
import { useAccess } from "../client/access";
import type { NewLiveWire } from "../generated/api";
import { camerasOf, type AddSeed } from "../live/cameras";
import { useLoginChecks } from "../live/checks";
import { informationOf } from "../live/information";
import { useNetwork, type Order } from "../live/network";
import {
  facetKey,
  offeredFacets,
  sectionsOf,
  viewsOf,
  type Facet,
  type Row,
  type RowAction,
} from "../live/rows";
import { useStreamStatuses } from "../live/streamState";
import { useSettings } from "../client/settings";
import { useCan, useCommand, useDoc } from "../session/session";
import { useShow, useShownItems } from "../viewer/useViewers";
import { useItemDialogs } from "./ItemDialogs";
import { useKept } from "../kit/keptState";
import { LiveToolbar } from "./LiveToolbar";
import { LiveTree } from "./LiveTree";
import { ScanSettings } from "./ScanSettings";
import { Refusal } from "../kit/Refusal";

const NO_FACETS: ReadonlySet<string> = new Set();

/**
 * The sidebar's Live view: a search with Sort and Filter, the workspace's cameras with their streams, and, for whoever
 * may use this machine's network, the cameras a scan found that the workspace does not hold, with the scan's settings
 * under them. Nothing connects until Start, which opens the stream's viewer; Stop closes it. Add on a found camera adds
 * its stream with the login the scan signed in with, which stays on the backend, and starts it; `onAdd` opens the Add
 * Live Stream dialog from a seed.
 */
export function LiveView({ onAdd }: { onAdd: (seed: AddSeed) => void }) {
  const scans = useAccess().owner;
  const mayAct = useCan("content");
  const { values } = useSettings();
  const items = useDoc()?.workspace.items;
  const command = useCommand();
  const show = useShow();
  const { snapshot, epoch, connected } = useNetwork(scans);
  const reported = useStreamStatuses();
  const viewed = useShownItems();
  // What the user set up stays while the window shows another view or hides the sidebar.
  const [query, setQuery] = useKept("live:query", "");
  const [order, setOrder] = useKept<Order>("live:order", "address");
  const [hidden, setHidden] = useKept("live:facets", NO_FACETS);
  // As in the other views; a stream has no way to be hidden, so for now it changes no row.
  const [showHidden, setShowHidden] = useKept("live:showHidden", false);
  const [problem, setProblem] = useState<string | null>(null);
  const { edit, inform, dialogs } = useItemDialogs();
  // How each stream resolved, which keeps it under its camera while it resolves again or fails.
  const known = useRef(new Map<string, NewLiveWire>());

  const cameras = useMemo(() => {
    for (const item of items ?? [])
      if (item.resolution.status === "Resolved" && item.resolution.content.kind === "LiveVideo") {
        const { recipe } = item.resolution.content;
        if (recipe) known.current.set(item.id, recipe);
      }
    return camerasOf(items ?? [], known.current);
  }, [items]);
  const checks = useLoginChecks(
    cameras,
    scans && values["scan.sign_in"] !== false,
    snapshot?.scan ?? 0,
  );
  const sections = useMemo(
    () =>
      sectionsOf({
        cameras,
        found: snapshot?.rows ?? [],
        scanned: snapshot !== null && (snapshot.scan > 0 || snapshot.scanning),
        reported,
        checks,
        thumbnails: scans,
        viewed,
        order,
      }),
    [cameras, snapshot, reported, checks, scans, viewed, order],
  );
  const views = useMemo(() => viewsOf(sections, query, hidden), [sections, query, hidden]);
  const facets = useMemo(() => offeredFacets(sections, hidden), [sections, hidden]);
  const noMatches =
    query.trim() !== "" &&
    !views.some(({ shown, filteredOut }) => shown.length > 0 || filteredOut > 0);

  const attempt = (work: Promise<unknown>) =>
    work.then(
      () => setProblem(null),
      (error: unknown) => setProblem(error instanceof Error ? error.message : String(error)),
    );
  const act = (row: Row, action: RowAction) => {
    const { item, like, found } = row.subject;
    // Start opens beside the open viewers, never in place of one; a stream already in a pane is brought forward.
    if (action === "start" && item)
      void attempt(show(item, undefined, { side: true, reuse: true }));
    else if (action === "stop" && item)
      void attempt(command({ type: "content", command: { type: "closeViewer", item } }));
    else if (action === "addStream") onAdd({ like });
    else if (action === "add" && found)
      void attempt(
        command({
          type: "content",
          command: {
            type: "addItems",
            items: [{ type: "Live", found: found.address }],
            open: true,
          },
        }),
      );
    else if (action === "remove" && item)
      void attempt(command({ type: "content", command: { type: "remove", item } }));
    else if (action === "information" && item) {
      const stream = cameras.flatMap((camera) => camera.streams).find(({ id }) => id === item);
      if (stream) inform(informationOf(stream));
    } else if (action === "edit" && item) {
      const stored = items?.find(({ id }) => id === item);
      if (stored) edit(stored);
    }
  };
  /** Enter or a double click: a stream that can start or stop starts, else comes to the front. */
  const open = (row: Row) => {
    if (mayAct && (row.menu.includes("start") || row.menu.includes("stop"))) act(row, "start");
  };
  const remove = (row: Row) => {
    if (mayAct && row.menu.includes("remove")) act(row, "remove");
  };
  const toggle = (facet: Facet) =>
    setHidden((now) => {
      const next = new Set(now);
      if (!next.delete(facetKey(facet))) next.add(facetKey(facet));
      return next;
    });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <LiveToolbar
        query={query}
        onQuery={setQuery}
        order={order}
        onOrder={setOrder}
        facets={facets}
        hidden={hidden}
        onToggle={toggle}
        showHidden={showHidden}
        onShowHidden={setShowHidden}
      />
      {noMatches && <p className="m-0 px-m pt-s text-small text-muted">No matches</p>}
      {problem && (
        <div className="px-m pt-s">
          <Refusal reason={problem} />
        </div>
      )}
      <LiveTree
        views={views}
        searching={query.trim() !== ""}
        mayAct={mayAct}
        onAddStream={() => onAdd({})}
        onOpen={open}
        onRemove={remove}
        onAct={act}
      />
      {scans && <ScanSettings snapshot={snapshot} epoch={epoch} connected={connected} />}
      {dialogs}
    </div>
  );
}
