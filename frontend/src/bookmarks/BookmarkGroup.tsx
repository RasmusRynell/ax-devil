import type { BookmarkGroupInfo, BookmarkInfo, BookmarkKindWire } from "../generated/api";
import { Chip } from "../kit/Chips";
import { ContextMenu } from "../kit/ContextMenu";
import { Icon } from "../kit/Icon";
import { KIND } from "../kit/look";
import type { MenuCommand, MenuEntry } from "../kit/Menu";
import { labelOf, KINDS, subtitleOf, titleOf } from "./groups";

/** The colour of a bookmark's icon: muted for a note, the live colour for an issue, then success and the accent. */
const MARKS: Record<BookmarkKindWire, string> = {
  note: "text-muted",
  issue: KIND.live,
  example: "text-success",
  question: "text-accent",
};

const RENAME = "rename";
const REMOVE = "remove";
const KIND_PREFIX = "kind:";

/** What a bookmark's menu offers: Rename, its Kind with the current one checked, and Remove. */
function menuOf(mayEdit: boolean): MenuEntry[] {
  return [
    { kind: "action", id: RENAME, label: "Rename", disabled: !mayEdit },
    {
      kind: "submenu",
      label: "Kind",
      items: KINDS.map(([kind, name]) => ({
        kind: "check",
        id: `${KIND_PREFIX}${kind}`,
        label: name,
        disabled: !mayEdit,
      })),
    },
    { kind: "separator" },
    { kind: "action", id: REMOVE, label: "Remove", disabled: !mayEdit },
  ];
}

/** What a bookmark's row asks of its owner. */
export interface RowActions {
  open: (group: BookmarkGroupInfo, bookmark: BookmarkInfo) => void;
  rename: (bookmark: BookmarkInfo) => void;
  kind: (bookmark: BookmarkInfo, kind: BookmarkKindWire) => void;
  remove: (bookmark: BookmarkInfo) => void;
  review: (group: BookmarkGroupInfo) => void;
}

/**
 * One video or entry with bookmarks or a reviewed mark: its title and review chip, then a row for each bookmark. A
 * click on a row opens its frame; the row's menu renames it, changes its kind or removes it. A group whose target is
 * gone is dimmed, its rows say Missing and do not open, and its review mark can only be cleared. While its item
 * resolves its rows do not open yet.
 */
export function BookmarkGroup({
  group,
  mayEdit,
  actions,
}: {
  group: BookmarkGroupInfo;
  mayEdit: boolean;
  actions: RowActions;
}) {
  const { standing } = group;
  const missing = standing.status === "Missing";
  const openable = standing.status === "Open";
  const title = titleOf(group);
  return (
    <section aria-label={title} className={`flex flex-col ${missing ? "opacity-60" : ""}`}>
      <div className="flex min-h-row items-center gap-m px-m">
        <Icon
          name={group.target === null ? "square-play" : "list-video"}
          className={group.target === null ? KIND.video : KIND.playlist}
        />
        <h3 title={title} className="m-0 min-w-0 flex-1 truncate font-semibold">
          {title}
        </h3>
        {(openable || group.reviewed) && (
          <Chip
            tone={group.reviewed ? "success" : "neutral"}
            onClick={mayEdit ? () => actions.review(group) : undefined}
            title={group.reviewed ? "Mark to review" : "Mark reviewed"}
          >
            {group.reviewed ? "Reviewed" : "To review"}
          </Chip>
        )}
      </div>
      {group.bookmarks.map((bookmark) => (
        <ContextMenu
          key={bookmark.id}
          entries={menuOf(mayEdit)}
          checked={new Set([`${KIND_PREFIX}${bookmark.kind}`])}
          onSelect={({ id }: MenuCommand) => {
            if (id === RENAME) actions.rename(bookmark);
            else if (id === REMOVE) actions.remove(bookmark);
            else actions.kind(bookmark, id.slice(KIND_PREFIX.length) as BookmarkKindWire);
          }}
        >
          <button
            type="button"
            data-cursor-id={`bookmark:${bookmark.id}`}
            // Not `disabled`: a row that cannot open still has its menu.
            aria-disabled={!openable}
            onClick={() => openable && actions.open(group, bookmark)}
            className="flex w-full min-w-0 items-center gap-m rounded-control bg-transparent py-xs pr-m pl-l text-left text-fg not-aria-disabled:hover:bg-hover focus-ring-inset aria-disabled:cursor-default"
          >
            <Icon name="bookmark" className={MARKS[bookmark.kind]} />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate">{labelOf(bookmark)}</span>
              <span className="truncate text-small text-muted">
                {missing ? "Missing" : subtitleOf(bookmark)}
              </span>
            </span>
          </button>
        </ContextMenu>
      ))}
    </section>
  );
}
