import { useEffect, useMemo, useState } from "react";
import type {
  BookmarkCommand,
  BookmarkGroupInfo,
  BookmarkInfo,
  BookmarkKindWire,
} from "../generated/api";
import { ChoiceChips } from "../kit/Chips";
import { SearchField } from "../kit/Fields";
import { IconButton } from "../kit/IconButton";
import { useCan, useCommand, useDoc } from "../session/session";
import { useKept } from "../kit/keptState";
import { useShow } from "../viewer/useViewers";
import { BookmarkGroup, type RowActions } from "./BookmarkGroup";
import { useExportBookmarks } from "./ExportBookmarks";
import { countOf, filterGroups, KINDS, labelOf, marksOf } from "./groups";
import { RenameBookmark } from "./RenameBookmark";
import { useAskSeek } from "./viewerLink";

type Filter = "all" | BookmarkKindWire;

const FILTERS: readonly (readonly [Filter, string])[] = [
  ["all", "All"],
  ...KINDS.filter(([kind]) => kind !== "note"),
  ["note", "Note"],
];

const NONE: readonly BookmarkGroupInfo[] = [];

/**
 * The bookmarks of the workspace's videos and playlist entries, a group for each. Above them Search, a filter by kind
 * and Export CSV, which writes every bookmark whatever the search and filter show. With no bookmark there is no
 * search, filter or export, only "No bookmarks"; with some but none matching, "No matches".
 */
export function BookmarksView() {
  const workspace = useDoc()?.workspace;
  const groups = workspace?.bookmarks ?? NONE;
  const command = useCommand();
  const show = useShow();
  const ask = useAskSeek();
  const mayEdit = useCan("content");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [renaming, setRenaming] = useState<BookmarkInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // The bookmark opened last is marked, through a switch to another view, until a bookmark or review mark changes or
  // another workspace replaces this one; a target that resolves again, in another order, leaves the mark alone.
  const marks = useMemo(() => marksOf(groups), [groups]);
  const epoch = workspace?.epoch;
  const [opened, setOpened] = useKept<{ id: string; marks: string; epoch?: number } | null>(
    "bookmarks:opened",
    null,
  );
  const current = opened?.marks === marks && opened.epoch === epoch ? opened : null;
  useEffect(() => {
    if (opened && !current) setOpened(null);
  }, [opened, current, setOpened]);
  const total = countOf(groups);
  // Search and kind are on screen only while a bookmark exists; without one they filter nothing, so a reviewed-only
  // group cannot be hidden by controls that are not there to reset.
  const filtering = total > 0;
  const shown = useMemo(
    () => (filtering ? filterGroups(groups, query, filter === "all" ? null : filter) : groups),
    [groups, query, filter, filtering],
  );

  const fail = (error: unknown) =>
    setProblem(error instanceof Error ? error.message : String(error));
  const exportCsv = useExportBookmarks(fail);
  const send = (change: BookmarkCommand) =>
    command({ type: "content", command: { type: "bookmark", command: change } }).then(
      () => setProblem(null),
      fail,
    );
  const actions: RowActions = {
    open: ({ item, target, standing }, { id, frame }) => {
      if (standing.status !== "Open") return;
      // A video's viewer shows its one entry; a playlist's is told which. A viewer already showing it is brought
      // forward, the front one first; else it opens as an ordinary Open does.
      show(item, target === null ? undefined : standing.entry, { reuse: true }).then(() => {
        setProblem(null);
        setOpened({ id, marks, epoch });
        ask({ entry: standing.entry, frame });
      }, fail);
    },
    rename: setRenaming,
    kind: ({ id }, kind) => void send({ type: "edit", id, kind }),
    remove: ({ id }) => void send({ type: "remove", id }),
    review: ({ item, target, reviewed }) =>
      void send({ type: "setReviewed", item, entry: target ?? undefined, reviewed: !reviewed }),
  };

  return (
    <section aria-label="Bookmarks" className="flex min-h-0 flex-1 flex-col gap-xs">
      {total > 0 && (
        <div className="flex shrink-0 flex-col gap-s px-s pt-s">
          <div className="flex items-center gap-s">
            <SearchField
              placeholder="Search bookmarks"
              query={{ value: query, onChange: setQuery }}
              className="min-w-search-least flex-1"
            />
            <IconButton icon="download" label="Export CSV" onClick={exportCsv} />
          </div>
          <ChoiceChips label="Kind" options={FILTERS} value={filter} onChange={setFilter} />
        </div>
      )}
      {problem && (
        <p role="alert" className="m-0 px-m break-words">
          {problem}
        </p>
      )}
      {groups.length === 0 ? (
        <p className="m-0 px-m text-muted">No bookmarks</p>
      ) : shown.length === 0 ? (
        <p className="m-0 px-m text-small text-muted">No matches</p>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {shown.map((group) => (
            <BookmarkGroup
              key={group.key}
              group={group}
              active={current?.id ?? null}
              mayEdit={mayEdit}
              actions={actions}
            />
          ))}
        </div>
      )}
      {renaming && (
        <RenameBookmark
          label={labelOf(renaming)}
          fallback={labelOf({ ...renaming, note: "" })}
          onRename={(note) => void send({ type: "edit", id: renaming.id, note })}
          onClose={() => setRenaming(null)}
        />
      )}
    </section>
  );
}
