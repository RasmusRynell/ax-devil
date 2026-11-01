import type { SidebarView } from "../generated/api";
import { SearchField } from "../kit/Fields";
import { SessionsView } from "./SessionsView";
import { StartPanel } from "./StartPanel";
import { WorkspaceTree } from "./WorkspaceTree";
import { SEARCH, VIEW_TITLES } from "./views";

/** The sidebar: a title, the view's search field where it has one, and the view's placeholder content. */
export function Sidebar({ view, onRun }: { view: SidebarView; onRun: (id: string) => void }) {
  const search = SEARCH[view];
  return (
    <aside
      aria-label="Sidebar"
      data-cursor-region="sidebar"
      className="flex w-sidebar max-w-full min-w-0 shrink-0 flex-col gap-m bg-panel max-sm:min-h-0 max-sm:w-full max-sm:flex-1 sm:max-w-2/5 sm:border-r sm:border-line"
    >
      <h2
        data-cursor-id="sidebar:title"
        className="m-0 flex h-header items-center px-m text-body font-semibold"
      >
        {VIEW_TITLES[view]}
      </h2>
      <div
        className={`flex min-h-0 flex-1 flex-col gap-m px-s pb-s ${view === "workspace" ? "" : "overflow-y-auto"}`}
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
        {view === "plugins" && (
          <p className="m-0 px-m text-muted">Plugin changes apply after a restart.</p>
        )}
      </div>
    </aside>
  );
}
