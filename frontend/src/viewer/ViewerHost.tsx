import { useMemo } from "react";
import type { ItemState } from "../generated/api";
import { IconButton } from "../kit/IconButton";
import { viewerFor } from "../viewers";
import { hiddenOf, type Hidden } from "./hidden";
import type { WorkspaceStore } from "../workspace/store";
import { useWorkspace } from "../workspace/WorkspaceProvider";

/** What shows for an item whose viewer is not ready: it is resolving, or it failed with a reason. */
function ItemStatus({ item }: { item: ItemState }) {
  const { resolution } = item;
  if (resolution.status === "Failed")
    return (
      <div role="alert" className="m-auto flex max-w-full flex-col gap-s p-xl text-center">
        <p className="m-0 font-semibold break-words">{resolution.reason}</p>
        <p className="m-0 text-small break-all text-muted">{resolution.location}</p>
      </div>
    );
  return (
    <p role="status" aria-busy="true" className="m-auto p-xl text-muted">
      Opening {item.label}…
    </p>
  );
}

function ItemView({
  item,
  store,
  hidden,
}: {
  item: ItemState;
  store: WorkspaceStore;
  hidden: Hidden;
}) {
  const { resolution } = item;
  if (resolution.status !== "Resolved") return <ItemStatus item={item} />;
  const Viewer = viewerFor(resolution.content);
  const memory = {
    saved: store.remembered(item.id),
    save: (state: unknown) => store.remember(item.id, state),
  };
  return (
    <Viewer
      key={resolution.content.id}
      content={resolution.content}
      memory={memory}
      hidden={hidden}
    />
  );
}

/** The central area while viewers are open: a tab for each, and the active item's viewer. */
export function ViewerHost() {
  const { view, store } = useWorkspace();
  const items = view.viewers.flatMap(
    (id) => view.state?.items.find((item) => item.id === id) ?? [],
  );
  const active = items.find((item) => item.id === view.active);
  const rows = view.state?.rows;
  const hidden = useMemo(() => hiddenOf(rows ?? []), [rows]);
  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      <div
        role="tablist"
        aria-label="Open items"
        className="flex h-row shrink-0 overflow-x-auto border-b border-line bg-panel [scrollbar-width:none]"
      >
        {items.map((item) => (
          <div
            key={item.id}
            className={`flex min-w-0 shrink-0 items-center ${item.id === active?.id ? "border-b-edge border-accent" : ""}`}
          >
            <button
              type="button"
              role="tab"
              id={`tab-${item.id}`}
              aria-selected={item.id === active?.id}
              onClick={() => store.activate(item.id)}
              className="h-row max-w-[24ch] truncate bg-transparent pr-xs pl-m text-fg hover:bg-hover focus-ring-inset aria-selected:font-semibold"
            >
              {item.label}
            </button>
            <IconButton
              icon="x"
              label={`Close ${item.label}`}
              onClick={() => store.close(item.id)}
              className="size-row hover:bg-hover"
            />
          </div>
        ))}
      </div>
      <div
        role="tabpanel"
        aria-labelledby={active && `tab-${active.id}`}
        className="flex min-h-0 flex-1 flex-col"
      >
        {active && <ItemView item={active} store={store} hidden={hidden} />}
      </div>
    </div>
  );
}
