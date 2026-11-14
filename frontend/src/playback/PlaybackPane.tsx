import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useAddBookmark, useSeekRequest } from "../bookmarks/viewerLink";
import { ExportDialog, ExportNotice } from "../export/ExportDialog";
import { choicesOf } from "../export/lanes";
import { ContextMenu } from "../kit/ContextMenu";
import { Icon } from "../kit/Icon";
import { NoticeList } from "../kit/NoticeList";
import { Popover } from "../kit/Popover";
import { useAllowed, useOverlays, useOwnerSeat } from "../session/session";
import { isPluginCommand } from "../client/commands";
import { useSettings } from "../client/settings";
import { MediaToolsPanel } from "../tools/MediaToolsPanel";
import { useMediaTools } from "../tools/useMediaTools";
import { PaneActiveContext, useHeaderDetails, useViewerActions } from "../viewer/actions";
import { usePluginCommands } from "../plugins/listing";
import { RunCommandContext } from "../plugins/RunDialog";
import { bindViewer, type BoundViewer } from "../viewer/bound";
import { registerExport } from "../viewer/exportTargets";
import type { Hidden } from "../viewer/hidden";
import { useLaneFocus } from "../viewer/laneFocus";
import { useChromeVisibility } from "./chromeVisibility";
import type { Viewport } from "../lanes/contract";
import type { InspectorMemory } from "../inspect/fields";
import { allHidden, shownLanes } from "./laneCells";
import { LaneGrid } from "./LaneGrid";
import { HIDE_LANE, laneContextEntries, OFFLINE_LANE_COMMANDS } from "../lanes/laneContextMenu";
import { useLaneFullscreen } from "./laneFullscreen";
import { ObjectLayer, type ObjectLayerHandle } from "./ObjectLayer";
import { Opening } from "./PlaybackStatus";
import type { Geometry } from "./renderer";
import type { PlaybackStream } from "./stream";
import { Transport } from "./Transport";
import { FIT, ZOOM_STEP } from "./viewport";

/** One entry's pane once its stream exists: the lanes, transport and tools while it is ready, else why not. */
export function Playback({
  stream,
  viewer,
  item,
  name,
  title,
  entry,
  hidden,
  canChange,
  onHideLane,
  viewport,
  onViewport,
  onDrawn,
  inspector,
  onInspector,
}: {
  stream: PlaybackStream;
  /** The id of the pane's viewer, which its export is offered under. */
  viewer: number;
  item: string;
  name: string;
  title: string;
  entry: string;
  hidden: Hidden;
  canChange: boolean;
  onHideLane?: (label: string) => void;
  viewport: Viewport;
  /** Asks for another viewport; absent when this window may not zoom or pan. */
  onViewport?: (viewport: Viewport) => void;
  /** Called with the geometry of the pictures after every draw. */
  onDrawn?: (geometry: Geometry) => void;
  inspector: InspectorMemory;
  onInspector: (memory: InspectorMemory) => void;
}) {
  const view = useSyncExternalStore(stream.subscribe, stream.getSnapshot);
  const { shortcuts } = useSettings();
  const { objectDetails, laneNames } = useOverlays();
  const inspectable = useOwnerSeat() || objectDetails;
  const allowed = useAllowed();
  const plugins = usePluginCommands();
  const runCommand = useContext(RunCommandContext);
  const pane = useRef<HTMLDivElement>(null);
  const objects = useRef<ObjectLayerHandle>(null);
  const geometry = useRef<Geometry | null>(null);
  // Export is open, for every lane or for the one the lane menu was used on.
  const [exporting, setExporting] = useState<{ lane: number | null } | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [tools, setTools] = useState(false);
  // The media tools' pages, lists and width, kept while they are closed; the selected object is the page on top.
  const { outlined, pick, chooseLane, panel } = useMediaTools(stream, objectDetails, tools, stream);
  // One switch for every lane; it lives here so it stays across entries.
  const [info, setInfo] = useState(false);
  useChromeVisibility(pane);
  const shown = useMemo(() => shownLanes(view.lanes, hidden, entry), [view.lanes, hidden, entry]);
  const fullscreen = useLaneFullscreen(pane, shown, stream);
  const shownViewport = useRef(viewport);
  shownViewport.current = viewport;
  // A viewer that opens in the active pane takes the keyboard, so its shortcuts work at once; one that opens behind it
  // leaves the keyboard and the active pane alone.
  const active = useContext(PaneActiveContext);
  const opensInFront = useRef(active);
  useEffect(() => {
    if (opensInFront.current) pane.current?.focus({ preventScroll: true });
  }, []);
  // A lane opened from the tree takes the keyboard: it is the lane the shortcuts and the lane menu act on.
  const labels = useMemo(() => view.lanes.map((lane) => lane.label), [view.lanes]);
  const { focus } = fullscreen;
  useLaneFocus(
    viewer,
    entry,
    labels,
    shown,
    active,
    useCallback(
      (lane: number) => {
        focus(lane);
        pane.current?.focus({ preventScroll: true });
      },
      [focus],
    ),
  );
  // The selected object's page is the one the media tools show, so its fields are asked for only while they are open.
  useEffect(
    () => stream.showInspector(tools && objectDetails, inspector.pins),
    [stream, tools, objectDetails, inspector.pins],
  );
  const zoom = useCallback(
    (factor: number) => {
      const next = geometry.current?.zoomAt(shownViewport.current, factor);
      if (!onViewport || !next || next === shownViewport.current) return;
      shownViewport.current = next;
      onViewport(next);
    },
    [onViewport],
  );
  const hover = useCallback(
    (picked: Parameters<ObjectLayerHandle["hover"]>[0]) => objects.current?.hover(picked),
    [],
  );
  const drawn = useCallback(
    (next: Geometry) => {
      geometry.current = next;
      objects.current?.drawn(next);
      onDrawn?.(next);
    },
    [onDrawn],
  );
  // An export covers the lanes the action is for; while a catalog of one is loading it only says so and starts nothing.
  const exportLane = useCallback(
    (lane: number | null) => {
      const current = stream.getSnapshot();
      if (current.status !== "ready") return;
      const covered = choicesOf(current.lanes).filter((c) => lane === null || c.lane === lane);
      if (covered.length === 0) return;
      if (covered.some((choice) => choice.cold === "loading")) {
        setWaiting(true);
        return;
      }
      stream.pause();
      setExporting({ lane });
    },
    [stream],
  );
  const ready = view.status === "ready";
  useEffect(
    () => (ready ? registerExport(viewer, item, entry, () => exportLane(null)) : undefined),
    [ready, viewer, item, entry, exportLane],
  );
  useHeaderDetails(view.details);
  // The plugin views the sidebar shows drive this viewer while it plays.
  const bound = useMemo<BoundViewer>(
    () => ({ viewer, item, entry, stream }),
    [viewer, item, entry, stream],
  );
  useEffect(() => (ready ? bindViewer(bound) : undefined), [ready, bound]);
  const addBookmark = useAddBookmark(stream, item, entry);
  useSeekRequest(stream, view.status, entry);
  // Opening the tools shows the lane the shortcuts act on.
  const toggleTools = useCallback(() => {
    if (!tools) {
      const lane = fullscreen.target();
      if (lane !== null) chooseLane(lane);
    }
    setTools((open) => !open);
  }, [tools, fullscreen.target, chooseLane]);
  const actions = useViewerActions(
    useMemo(
      () => ({
        "zoom-in": () => zoom(ZOOM_STEP),
        "zoom-out": () => zoom(1 / ZOOM_STEP),
        "zoom-reset": () => onViewport?.(FIT),
        "play-pause": () => stream.togglePlay(),
        "step-forward": () => stream.step(1),
        "step-back": () => stream.step(-1),
        "step-forward-10": () => stream.step(10),
        "step-back-10": () => stream.step(-10),
        "speed-up": () => stream.stepSpeed(1),
        "speed-down": () => stream.stepSpeed(-1),
        "media-tools": toggleTools,
        "info-overlay": () => setInfo((shown) => !shown),
        "lane-fullscreen": fullscreen.toggle,
        "export-video": () => exportLane(null),
        "add-bookmark": addBookmark,
      }),
      [
        stream,
        zoom,
        onViewport,
        fullscreen.toggle,
        fullscreen.target,
        exportLane,
        addBookmark,
        toggleTools,
      ],
    ),
  );
  return (
    <div
      ref={pane}
      tabIndex={-1}
      data-popover-bounds=""
      className="group relative flex min-h-0 flex-1 flex-col bg-canvas text-on-canvas outline-none @container"
      onKeyDown={(event) => {
        // An Escape a popup closed is spent, and in fullscreen Escape only leaves it.
        const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
        if (event.key === "Escape" && plain && !event.nativeEvent.defaultPrevented)
          if (!fullscreen.leave()) pick(null);
      }}
    >
      {view.status === "ready" ? (
        <div className="flex min-h-0 flex-1">
          <div data-chrome-idle="" className="relative min-h-0 min-w-0 flex-1">
            <ContextMenu
              className="absolute inset-0"
              entries={() => {
                const lane = fullscreen.target();
                const cold = choicesOf(view.lanes).find((choice) => choice.lane === lane)?.cold;
                return laneContextEntries(
                  OFFLINE_LANE_COMMANDS,
                  allowed,
                  shortcuts,
                  onHideLane !== undefined,
                  !!cold,
                  plugins,
                );
              }}
              onSelect={({ id }) => {
                const lane = fullscreen.target();
                if (id === HIDE_LANE) {
                  if (lane !== null) onHideLane?.(view.lanes[lane].label);
                } else if (id === "export-video") exportLane(lane);
                else if (isPluginCommand(id))
                  runCommand(id, {
                    item,
                    entry,
                    slot: lane === null ? undefined : view.lanes[lane].slot,
                  });
                else actions[id]?.();
              }}
            >
              <LaneGrid
                stream={stream}
                view={view}
                name={name}
                title={title}
                bound={bound}
                anchor={`frame:${item}`}
                entry={entry}
                hidden={hidden}
                only={fullscreen.solo}
                info={info}
                onHide={onHideLane}
                onExport={allowed.host ? exportLane : undefined}
                onPoint={fullscreen.point}
                onPick={fullscreen.pick}
                viewport={viewport}
                onViewport={onViewport}
                objectDetails={objectDetails}
                laneNames={laneNames}
                onHover={hover}
                onSelect={pick}
                onDrawn={drawn}
              />
            </ContextMenu>
            <ObjectLayer
              selection={outlined ? view.selection : null}
              objectDetails={objectDetails}
              handle={objects}
            />
            {view.warnings.length > 0 && (
              <div data-keeps-chrome="warnings" className="absolute top-0 right-0 p-s">
                <div className="rounded-control bg-canvas-scrim">
                  <Popover
                    onCanvas
                    label={`${name}: warnings`}
                    trigger={<Icon name="triangle-alert" />}
                    className="size-row justify-center"
                  >
                    <NoticeList notices={view.warnings.map((problem) => ({ problem }))} />
                  </Popover>
                </div>
              </div>
            )}
            {/* With every lane hidden there is nothing to play: no transport, so no pick or press outlives the lanes. */}
            {!allHidden(view.lanes, shown) && (
              <Transport
                controls={stream}
                view={view}
                disabled={!canChange}
                tools={{
                  open: tools,
                  shortcut: shortcuts["media-tools"] ?? null,
                  onToggle: toggleTools,
                }}
              />
            )}
          </div>
          {tools && (
            <MediaToolsPanel
              {...panel}
              source={stream}
              view={view}
              canSeek={canChange}
              selectable={objectDetails}
              inspectable={inspectable}
              memory={inspector}
              onMemory={onInspector}
              onClose={() => setTools(false)}
            />
          )}
        </div>
      ) : (
        <Opening view={view} name={name} />
      )}
      {waiting && <ExportNotice onClose={() => setWaiting(false)} />}
      {exporting && (
        <ExportDialog
          only={exporting.lane ?? undefined}
          jobs={stream.exports}
          view={view}
          name={name}
          onClose={() => setExporting(null)}
        />
      )}
    </div>
  );
}
