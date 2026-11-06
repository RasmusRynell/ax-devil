import { useState } from "react";
import { reasonOf } from "../errors";
import type { ItemState } from "../generated/api";
import { IconButton } from "../kit/IconButton";
import { QUIET } from "../kit/look";
import { useCan, useCommand, useView } from "../session/session";
import { viewerFor } from "../viewers";
import { ReloadOffer } from "./ReloadOffer";
import { useViewers, type Viewer } from "./useViewers";

/** What shows for an item whose viewer is not ready: it is resolving, or it failed with a reason. */
function ItemStatus({ item }: { item: ItemState }) {
  const { resolution } = item;
  if (resolution.status === "Failed")
    return (
      <div role="alert" className="m-auto flex max-w-full flex-col gap-s p-xl text-center">
        <p className="m-0 font-semibold break-words">{reasonOf(resolution)}</p>
        {resolution.location && (
          <p className="m-0 text-small break-all text-muted">{resolution.location}</p>
        )}
      </div>
    );
  return (
    <p role="status" aria-busy="true" className="m-auto p-xl text-muted">
      Opening {item.label}…
    </p>
  );
}

/** `dismissed` is the reload offers the user took down, by item and generation; the host keeps it across tabs. */
function ItemView({
  viewer,
  dismissed,
  onDismiss,
}: {
  viewer: Viewer;
  dismissed: Readonly<Record<string, number>>;
  onDismiss: (offer: string, count: number) => void;
}) {
  const { item } = viewer;
  const { resolution } = item;
  if (resolution.status !== "Resolved") return <ItemStatus item={item} />;
  const { content } = resolution;
  const Viewer = viewerFor(content);
  // A reload is a new generation, whose changes count from none again.
  const offer = `${item.id}.${content.generation}`;
  // A new generation is a new stream: closing and reopening the item shows a fresh viewer.
  return (
    <>
      <ReloadOffer
        item={item.id}
        changed={item.changed ?? 0}
        dismissed={dismissed[offer] ?? 0}
        onDismiss={(count) => onDismiss(offer, count)}
      />
      <Viewer
        key={`${viewer.id}.${content.id}.${content.generation}`}
        item={item.id}
        viewer={viewer.id}
        content={content}
      />
    </>
  );
}

/** The central area while viewers are open: a tab for each, and the active viewer. */
export function ViewerHost() {
  const { viewers, active } = useViewers();
  const [, setActive] = useView("active");
  const command = useCommand();
  const closable = useCan("content");
  const [dismissed, setDismissed] = useState<Readonly<Record<string, number>>>({});
  const dismiss = (offer: string, count: number) =>
    setDismissed((now) => ({ ...now, [offer]: count }));
  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      <div
        role="tablist"
        aria-label="Open items"
        className="flex h-row shrink-0 overflow-x-auto border-b border-line bg-panel [scrollbar-width:none]"
      >
        {viewers.map(({ id, item }) => (
          <div
            key={id}
            className={`flex min-w-0 shrink-0 items-center ${id === active?.id ? "border-b-edge border-accent" : ""}`}
          >
            <button
              type="button"
              role="tab"
              id={`tab-${id}`}
              data-cursor-id={`tab:${item.id}`}
              aria-selected={id === active?.id}
              onClick={() => setActive(id)}
              className={`${QUIET} max-w-[24ch] focus-ring-inset aria-selected:font-semibold`}
            >
              <span className="truncate">{item.label}</span>
            </button>
            <IconButton
              icon="x"
              label={`Close ${item.label}`}
              data-cursor-id={`tab-close:${item.id}`}
              disabled={!closable}
              // A refused close leaves the tab open, which says so.
              onClick={() =>
                void command({
                  type: "content",
                  command: { type: "closeViewer", item: item.id },
                }).catch(() => undefined)
              }
            />
          </div>
        ))}
      </div>
      <div
        role="tabpanel"
        aria-labelledby={active && `tab-${active.id}`}
        className="flex min-h-0 flex-1 flex-col"
      >
        {active && <ItemView viewer={active} dismissed={dismissed} onDismiss={dismiss} />}
      </div>
    </div>
  );
}
