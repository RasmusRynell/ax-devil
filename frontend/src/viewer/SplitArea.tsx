import {
  memo,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { flushSync } from "react-dom";
import { reasonOf } from "../errors";
import type { ItemState } from "../generated/api";
import { useCan, useSetView, useView } from "../session/session";
import { viewerFor } from "../viewers";
import { prune, rects, resize, seams, type Path, type Size } from "./layout";
import { activated, moved, pinned } from "./placement";
import { Pane } from "./Pane";
import { ReloadOffer } from "./ReloadOffer";
import { Seam } from "./Seam";
import { useClosePanes, useViewers, type Viewer } from "./useViewers";

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

/**
 * The content of one pane. `dismissed` is the reload offers the user took down, by item and generation; the area
 * keeps it across panes. It renders only for its own props, so a seam being dragged does not redraw the viewers.
 */
const PaneContent = memo(function PaneContent({
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
});

/** The size of the element in CSS pixels, kept as it resizes. */
function useSize(): [Size, RefObject<HTMLDivElement | null>] {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const read = () => {
      const box = element.getBoundingClientRect();
      const [w, h] = [Math.round(box.width), Math.round(box.height)];
      setSize((now) => (now.w === w && now.h === h ? now : { w, h }));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    // [AI] Applied before the frame is drawn, so the panes never trail the area for a frame while it is resized.
    const observer = new ResizeObserver(() => flushSync(read));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [size, ref];
}

/**
 * The central area while viewers are open: a pane for each, laid out as the window's view says. The panes are
 * siblings, each placed from the rectangle its place in the layout gives it, never nested like the layout: a pane
 * that moves or is split keeps its place in the document, so its viewer neither remounts nor restarts. A seam
 * shows a share while it is dragged and the view takes it when the drag ends.
 */
export function SplitArea() {
  const { viewers, active } = useViewers();
  const [layout] = useView("layout");
  const setView = useSetView();
  const closePanes = useClosePanes();
  const mayArrange = useCan("view");
  const mayClose = useCan("content");
  const [dismissed, setDismissed] = useState<Readonly<Record<string, number>>>({});
  const dismiss = useCallback(
    (offer: string, count: number) => setDismissed((now) => ({ ...now, [offer]: count })),
    [],
  );
  const [size, area] = useSize();
  const [dragging, setDragging] = useState<{ path: Path; share: number } | null>(null);
  const dragged = useRef<number | null>(null);

  const placed = useMemo(
    () => prune(layout, (id) => viewers.some((viewer) => viewer.id === id)),
    [layout, viewers],
  );
  const shown = placed && dragging ? resize(placed, dragging.path, dragging.share) : placed;
  const boxes = useMemo(() => rects(shown, size), [shown, size]);
  const bars = useMemo(() => seams(shown, size), [shown, size]);

  const onResize = (path: Path, share: number, final: boolean) => {
    if (!final) return setDragging({ path, share });
    setDragging(null);
    if (placed) setView((now) => ({ ...now, layout: resize(placed, path, share) }));
  };

  return (
    <div ref={area} className="relative h-full min-h-0 w-full min-w-0 overflow-hidden">
      {viewers.map((viewer) => {
        const box = boxes.get(viewer.id);
        return (
          box && (
            <Pane
              key={viewer.id}
              viewer={viewer.id}
              title={viewer.item.label}
              pinned={viewer.pinned}
              active={viewer.id === active?.id}
              rect={box}
              canPin={mayArrange}
              canClose={mayClose}
              dragged={dragged}
              onActivate={() => setView((now) => activated(now, viewer.id))}
              onPin={(pin) => setView((now) => pinned(now, viewer.id, pin))}
              onClose={() => closePanes([viewer.id])}
              onDrop={(source, side) => setView((now) => moved(now, source, viewer.id, side))}
            >
              <PaneContent viewer={viewer} dismissed={dismissed} onDismiss={dismiss} />
            </Pane>
          )
        );
      })}
      {bars.map((seam) => (
        <Seam key={seam.path.join("")} seam={seam} onResize={onResize} />
      ))}
    </div>
  );
}
