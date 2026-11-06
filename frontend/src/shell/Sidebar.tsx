import { useMemo, type CSSProperties } from "react";
import { BookmarksView } from "../bookmarks/BookmarksView";
import { countOf } from "../bookmarks/groups";
import type { SidebarView } from "../generated/api";
import type { AddSeed } from "../live/cameras";
import { ViewPanel } from "../kit/ViewPanel";
import { useCan, useDoc, useRenameWorkspace, useView } from "../session/session";
import { PluginsView } from "../plugins/PluginsView";
import { PanelDivider } from "../tools/PanelDivider";
import { useKept } from "./keptState";
import { LiveView } from "./LiveView";
import { useStreaming } from "../live/cameras";
import { SessionsView } from "./SessionsView";
import { StartPanel } from "./StartPanel";
import { listedCount, treeOf, type SectionId } from "./treeModel";
import { WorkspaceTree } from "./WorkspaceTree";
import { UNTITLED } from "./useWindowTitle";
import { VIEW_TITLES } from "./views";

/** The views that list the workspace's items: the sections each lists, its search placeholder and its empty message. */
const LISTINGS: Partial<
  Record<SidebarView, { listed: readonly SectionId[]; search: string; empty: string }>
> = {
  workspace: {
    listed: ["live", "videos", "playlists", "other"],
    search: "Search items",
    empty: "No items",
  },
  offline: {
    listed: ["videos", "playlists"],
    search: "Search files",
    empty: "No videos or playlists",
  },
};

/** The views that list the workspace's items. */
export const LISTED_VIEWS = Object.keys(LISTINGS) as SidebarView[];

/**
 * The sidebar: a title and the view's content; the Live view has its own toolbar and says in the header how many
 * streams run, the Offline view how many videos and playlists it lists. The Workspace view is titled with the workspace's name,
 * `Untitled` without one, and a dot while it has unsaved changes. Its right edge drags: between what the view needs
 * and half the space beside the activity bar, remembered while the window lives, and a drag to nothing hides it.
 */
export function Sidebar({
  view,
  onRun,
  onAddLive,
}: {
  view: SidebarView;
  onRun: (id: string) => void;
  onAddLive: (seed: AddSeed) => void;
}) {
  const listing = LISTINGS[view];
  // The width the user dragged to, kept while the sidebar is hidden and shown again.
  const [width, setWidth] = useKept<number | null>("sidebar:width", null);
  const [, setShell] = useView("sidebar");
  const streaming = useStreaming();
  const workspace = useDoc()?.workspace;
  const offline = useMemo(
    () =>
      workspace && view === "offline" && listing
        ? listedCount(treeOf(workspace), listing.listed)
        : 0,
    [workspace, view, listing],
  );
  const marked = useMemo(
    () => (workspace && view === "bookmarks" ? countOf(workspace.bookmarks) : 0),
    [workspace, view],
  );
  const named = view === "workspace" ? workspace : undefined;
  const rename = useRenameWorkspace();
  const canEdit = useCan("content");
  return (
    <aside
      aria-label="Sidebar"
      data-cursor-region="sidebar"
      style={width === null ? undefined : ({ "--sidebar-width": `${width}px` } as CSSProperties)}
      className="relative flex shrink-0 flex-col max-sm:min-h-0 max-sm:w-full max-sm:flex-1 sm:w-[var(--sidebar-width,var(--spacing-sidebar))] sm:max-w-[calc((100%-var(--spacing-activity))/2)] sm:min-w-sidebar-least sm:border-r sm:border-line"
    >
      <PanelDivider
        label="Resize sidebar"
        dock="left"
        className="max-sm:hidden"
        onResize={setWidth}
        onClose={() => setShell((shown) => ({ ...shown, open: false }))}
      />
      <ViewPanel
        key={named?.epoch}
        title={named ? (named.name ?? UNTITLED) : VIEW_TITLES[view]}
        tooltip={named ? (named.file ?? undefined) : undefined}
        edit={
          named && canEdit
            ? { label: "Workspace name", onEdit: rename.edit, onDone: rename.done }
            : undefined
        }
        cursorId="sidebar:title"
        action={
          view === "live" && streaming > 0 ? (
            <span className="text-small text-muted">{`${streaming} streaming`}</span>
          ) : offline + marked > 0 ? (
            <span className="text-small text-muted">{offline + marked}</span>
          ) : named?.modified ? (
            <span
              role="img"
              aria-label="Unsaved changes"
              title="Unsaved changes"
              className="size-m shrink-0 rounded-full bg-accent"
            />
          ) : undefined
        }
      >
        {view === "live" ? (
          <LiveView onAdd={onAddLive} />
        ) : (
          <div
            className={`flex flex-col gap-m px-s pb-s ${listing || view === "bookmarks" ? "min-h-0 flex-1" : ""}`}
          >
            {view === "workspace" && <StartPanel onRun={onRun} />}
            {listing && <WorkspaceTree key={view} view={view} {...listing} />}
            {view === "bookmarks" && <BookmarksView />}
            {view === "sessions" && <SessionsView />}
            {view === "plugins" && <PluginsView />}
          </div>
        )}
      </ViewPanel>
    </aside>
  );
}
