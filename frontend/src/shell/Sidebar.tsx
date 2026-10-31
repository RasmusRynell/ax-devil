import { SearchField } from "../kit/Fields";
import { StartPanel } from "./StartPanel";
import { SEARCH, VIEW_TITLES, type ViewId } from "./views";

/** The sidebar: a title, the view's search field where it has one, and the view's placeholder content. */
export function Sidebar({ view, onRun }: { view: ViewId; onRun: (id: string) => void }) {
  const search = SEARCH[view];
  return (
    <aside
      aria-label="Sidebar"
      className="flex w-sidebar max-w-full min-w-0 shrink-0 flex-col gap-m bg-panel max-sm:min-h-0 max-sm:w-full max-sm:flex-1 sm:max-w-2/5 sm:border-r sm:border-line"
    >
      <h2 className="m-0 flex h-header items-center px-m text-body font-semibold">
        {VIEW_TITLES[view]}
      </h2>
      <div className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto px-s pb-s">
        {search && <SearchField placeholder={search} />}
        {view === "workspace" && <StartPanel onRun={onRun} />}
        {view === "bookmarks" && <p className="m-0 px-m text-muted">No bookmarks</p>}
        {view === "plugins" && (
          <p className="m-0 px-m text-muted">Plugin changes apply after a restart.</p>
        )}
      </div>
    </aside>
  );
}
