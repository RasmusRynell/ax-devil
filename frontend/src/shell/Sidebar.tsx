import type { SidebarView } from "../generated/api";
import { SearchField } from "../kit/Fields";
import { ViewPanel } from "../kit/ViewPanel";
import { useDoc } from "../session/session";
import { PluginsView } from "../plugins/PluginsView";
import { SessionsView } from "./SessionsView";
import { StartPanel } from "./StartPanel";
import { WorkspaceTree } from "./WorkspaceTree";
import { UNTITLED } from "./useWindowTitle";
import { SEARCH, VIEW_TITLES } from "./views";

/**
 * The sidebar: a title, the view's search field where it has one, and the view's placeholder content. The Workspace
 * view is titled with the workspace's name, `Untitled` without one, and a dot while it has unsaved changes.
 */
export function Sidebar({ view, onRun }: { view: SidebarView; onRun: (id: string) => void }) {
  const search = SEARCH[view];
  const workspace = useDoc()?.workspace;
  const named = view === "workspace" ? workspace : undefined;
  return (
    <aside
      aria-label="Sidebar"
      data-cursor-region="sidebar"
      className="flex w-sidebar max-w-full min-w-0 shrink-0 flex-col max-sm:min-h-0 max-sm:w-full max-sm:flex-1 sm:max-w-2/5 sm:border-r sm:border-line"
    >
      <ViewPanel
        title={named ? (named.name ?? UNTITLED) : VIEW_TITLES[view]}
        cursorId="sidebar:title"
        action={
          named?.modified ? (
            <span
              role="img"
              aria-label="Modified"
              className="size-m shrink-0 rounded-full bg-accent"
            />
          ) : undefined
        }
      >
        <div
          className={`flex flex-col gap-m px-s pb-s ${view === "workspace" ? "min-h-0 flex-1" : ""}`}
        >
          {search && <SearchField placeholder={search} />}
          {view === "workspace" && (
            <>
              <StartPanel onRun={onRun} />
              <WorkspaceTree />
            </>
          )}
          {view === "bookmarks" && <p className="m-0 px-m text-muted">No bookmarks</p>}
          {view === "sessions" && <SessionsView />}
          {view === "plugins" && <PluginsView />}
        </div>
      </ViewPanel>
    </aside>
  );
}
