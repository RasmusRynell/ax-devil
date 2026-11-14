import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSettings } from "../client/settings";
import { basisOf } from "../lanes/lanes";
import { useViewers } from "../viewer/useViewers";
import {
  createBus,
  onFrameLoad,
  type Binding,
  type Bus,
  type BusLane,
  type PluginView,
} from "./bus";
import { useBoundViewer, useBusHost, type BoundViewer } from "./usePlugins";

type Rect = { x: number; y: number; width: number; height: number };

const unchanging = () => () => {};
const noSnapshot = () => undefined;
const noCapture = () => {};

/** The theme a view is told: the setting, with the system's choice for `auto`. */
function useTheme(): "light" | "dark" {
  const preference = useSettings().values["ui.theme"];
  if (preference === "light" || preference === "dark") return preference;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * A plugin view in a sandboxed frame, bound to the viewer `target` (none: no viewer). The frame's first load gets the
 * bus's port; a view that loads another page loses it and is not shown again. `capture` is whether the view has the
 * pointer, which `viewer.set` changes through `onCapture`; `picture` is the video's rectangle in the frame.
 */
export function PluginFrame({
  view,
  target,
  capture = false,
  onCapture = noCapture,
  picture,
}: {
  view: PluginView;
  target: BoundViewer | undefined;
  capture?: boolean;
  onCapture?: (capture: boolean) => void;
  picture?: Rect;
}) {
  const host = useBusHost(target, capture, onCapture);
  const theme = useTheme();
  const frame = useRef<HTMLIFrameElement>(null);
  const [bus, setBus] = useState<Bus | null>(null);
  const [left, setLeft] = useState(false);
  const stream = target?.stream;
  const playing = useSyncExternalStore(
    stream ? stream.subscribe : unchanging,
    stream ? stream.getSnapshot : noSnapshot,
  );
  // One handler for the frame's life: its first load is the one that gets the port.
  const [load] = useState(() =>
    onFrameLoad(
      () => {
        const made = createBus(view, host);
        setBus(made);
        return made;
      },
      (port) => frame.current?.contentWindow?.postMessage("ax-devil", "*", [port]),
      () => {
        setBus(null);
        setLeft(true);
      },
    ),
  );
  useEffect(() => () => bus?.close(), [bus]);

  const lanes = useMemo<BusLane[]>(
    () =>
      (playing?.lanes ?? []).map((lane) => ({
        slot: lane.slot,
        label: lane.label,
        basis: basisOf(lane) ?? null,
        complete:
          lane.status === "plain" ||
          (lane.status.status === "Ready" && lane.status.ingest.state === "Complete"),
      })),
    [playing?.lanes],
  );
  const picked = playing?.selection;
  const entity = picked?.entity;
  const selection = useMemo(
    () => (picked && entity ? { lane: playing.lanes[picked.lane]?.slot ?? "", entity } : null),
    // The selection is its lane and entity; the snapshot around it changes with every frame.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
    [picked?.lane, entity],
  );
  // Another viewer, item or entry is another binding: calls made for the old one are stale.
  const epoch = useRef({ key: "", value: 0 });
  const key = target ? `${target.viewer}:${target.item}:${target.entry}` : "";
  useEffect(() => {
    if (!bus) return;
    if (epoch.current.key !== key) epoch.current = { key, value: epoch.current.value + 1 };
    const binding: Binding = {
      epoch: epoch.current.value,
      item: target?.item ?? null,
      entry: target?.entry ?? null,
      lanes,
      selection,
      theme,
      video: picture && { ...picture },
    };
    bus.rebind(binding);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [bus, key, lanes, selection, theme, picture?.x, picture?.y, picture?.width, picture?.height]);
  const frameNow = playing?.playhead;
  const timeUs = playing?.shown?.timestampUs ?? null;
  const isPlaying = playing?.playing ?? false;
  useEffect(() => {
    if (bus && frameNow !== undefined)
      bus.playhead({ frame: frameNow, timeUs, playing: isPlaying });
  }, [bus, frameNow, timeUs, isPlaying]);

  if (left)
    return (
      <p role="status" className="p-m text-muted">
        View left its page
      </p>
    );
  return (
    <iframe
      ref={frame}
      title={view.view.title}
      sandbox="allow-scripts"
      src={view.view.url}
      onLoad={load}
      className="size-full border-0"
    />
  );
}

/** A sidebar plugin view, bound to the viewer in front. */
export function SidebarFrame({ view }: { view: PluginView }) {
  const { active } = useViewers();
  const target = useBoundViewer(active?.id);
  return <PluginFrame key={view.view.url} view={view} target={target} />;
}

/**
 * A plugin view over one lane's picture, inside the lane's cell: it takes no pointer until it asks with
 * `viewer.set {capture: true}`, and Escape gives the pointer back. `cell` and `picture` are in the lane grid's coordinates.
 */
export function VideoLayer({
  view,
  target,
  cell,
  picture,
}: {
  view: PluginView;
  target: BoundViewer;
  cell: Rect;
  picture: Rect;
}) {
  const [capture, setCapture] = useState(false);
  useEffect(() => {
    if (!capture) return;
    const release = (event: KeyboardEvent) => event.key === "Escape" && setCapture(false);
    document.addEventListener("keydown", release);
    return () => document.removeEventListener("keydown", release);
  }, [capture]);
  return (
    <div
      className={`absolute ${capture ? "" : "pointer-events-none"}`}
      style={{ left: cell.x, top: cell.y, width: cell.width, height: cell.height }}
    >
      <PluginFrame
        key={view.view.url}
        view={view}
        target={target}
        capture={capture}
        onCapture={setCapture}
        picture={{
          x: picture.x - cell.x,
          y: picture.y - cell.y,
          width: picture.width,
          height: picture.height,
        }}
      />
    </div>
  );
}
