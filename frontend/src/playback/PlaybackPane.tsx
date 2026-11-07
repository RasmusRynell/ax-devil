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
import { useAllowed } from "../session/session";
import { useSettings } from "../settings";
import { MediaToolsPanel } from "../tools/MediaToolsPanel";
import { jump } from "../tools/pageStack";
import { useMediaTools } from "../tools/useMediaTools";
import { PaneActiveContext, useHeaderDetails, useViewerActions } from "../viewer/actions";
import { registerExport } from "../viewer/exportTargets";
import type { Hidden } from "../viewer/hidden";
import { useChromeVisibility } from "./chromeVisibility";
import type { Viewport } from "./contract";
import type { InspectorMemory } from "./fields";
import { allHidden, shownLanes } from "./laneCells";
import { LaneGrid } from "./LaneGrid";
import { HIDE_LANE, laneContextEntries, OFFLINE_LANE_COMMANDS } from "./laneContextMenu";
import { useLaneFullscreen } from "./laneFullscreen";
import { ObjectLayer, type ObjectLayerHandle } from "./ObjectLayer";
import { Opening } from "./PlaybackStatus";
import type { Geometry } from "./renderer";
import type { PlaybackSession } from "./session";
import { Transport } from "./Transport";
import { FIT, ZOOM_STEP } from "./viewport";

/** One entry's pane once its session exists: the lanes, transport and tools while it is ready, else why not. */
export function Playback({
  session,
  item,
  name,
  title,
  entry,
  hidden,
  canChange,
  onHideLane,
  viewport,
  onViewport,
  inspector,
  onInspector,
}: {
  session: PlaybackSession;
  item: string;
  name: string;
  title: string;
  entry: string;
  hidden: Hidden;
  canChange: boolean;
  onHideLane?: (label: string) => void;
  viewport: Viewport;
  onViewport: (viewport: Viewport) => void;
  inspector: InspectorMemory;
  onInspector: (memory: InspectorMemory) => void;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { values, shortcuts } = useSettings();
  const objectDetails = values["overlays.object_details"] === true;
  const allowed = useAllowed();
  const pane = useRef<HTMLDivElement>(null);
  const objects = useRef<ObjectLayerHandle>(null);
  const geometry = useRef<Geometry | null>(null);
  // Export is open, for every lane or for the one the lane menu was used on.
  const [exporting, setExporting] = useState<{ lane: number | null } | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [tools, setTools] = useState(false);
  // The media tools' pages, lists and width, kept while they are closed; the selected object is the page on top.
  const { pages, outlined, setOutlined, navigate, panel } = useMediaTools(session, objectDetails);
  // One switch for every lane; it lives here so it stays across entries.
  const [info, setInfo] = useState(false);
  useChromeVisibility(pane);
  const shown = useMemo(() => shownLanes(view.lanes, hidden, entry), [view.lanes, hidden, entry]);
  const fullscreen = useLaneFullscreen(pane, shown);
  const shownViewport = useRef(viewport);
  shownViewport.current = viewport;
  // A viewer that opens in the active pane takes the keyboard, so its shortcuts work at once; one that opens behind it
  // leaves the keyboard and the active pane alone.
  const opensInFront = useRef(useContext(PaneActiveContext));
  useEffect(() => {
    if (opensInFront.current) pane.current?.focus({ preventScroll: true });
  }, []);
  // The selected object's page is the one the media tools show, so its fields are asked for only while they are open.
  useEffect(() => session.showInspector(tools), [session, tools]);
  const zoom = useCallback(
    (factor: number) => {
      const next = geometry.current?.zoomAt(shownViewport.current, factor);
      if (!next || next === shownViewport.current) return;
      shownViewport.current = next;
      onViewport(next);
    },
    [onViewport],
  );
  const hover = useCallback(
    (picked: Parameters<ObjectLayerHandle["hover"]>[0]) => objects.current?.hover(picked),
    [],
  );
  const drawn = useCallback((next: Geometry) => {
    geometry.current = next;
    objects.current?.drawn(next);
  }, []);
  // An export covers the lanes the action is for; while a catalog of one is loading it only says so and starts nothing.
  const exportLane = useCallback(
    (lane: number | null) => {
      const current = session.getSnapshot();
      if (current.status !== "ready") return;
      const covered = choicesOf(current.lanes).filter((c) => lane === null || c.lane === lane);
      if (covered.length === 0) return;
      if (covered.some((choice) => choice.cold === "loading")) {
        setWaiting(true);
        return;
      }
      session.pause();
      setExporting({ lane });
    },
    [session],
  );
  const ready = view.status === "ready";
  useEffect(
    () => (ready ? registerExport(item, entry, () => exportLane(null)) : undefined),
    [ready, item, entry, exportLane],
  );
  useHeaderDetails(view.details);
  const addBookmark = useAddBookmark(session, item, entry);
  useSeekRequest(session, view.status, entry);
  const actions = useViewerActions(
    useMemo(
      () => ({
        "zoom-in": () => zoom(ZOOM_STEP),
        "zoom-out": () => zoom(1 / ZOOM_STEP),
        "zoom-reset": () => onViewport(FIT),
        "play-pause": () => session.togglePlay(),
        "step-forward": () => session.step(1),
        "step-back": () => session.step(-1),
        "step-forward-10": () => session.step(10),
        "step-back-10": () => session.step(-10),
        "speed-up": () => session.stepSpeed(1),
        "speed-down": () => session.stepSpeed(-1),
        "media-tools": () => setTools((open) => !open),
        "info-overlay": () => setInfo((shown) => !shown),
        "lane-fullscreen": fullscreen.toggle,
        "export-video": () => exportLane(null),
        "add-bookmark": addBookmark,
      }),
      [session, zoom, onViewport, fullscreen.toggle, exportLane, addBookmark],
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
          if (!fullscreen.leave()) setOutlined(false);
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
                );
              }}
              onSelect={({ id }) => {
                const lane = fullscreen.target();
                if (id === HIDE_LANE) {
                  if (lane !== null) onHideLane?.(view.lanes[lane].label);
                } else if (id === "export-video") exportLane(lane);
                else actions[id]?.();
              }}
            >
              <LaneGrid
                session={session}
                view={view}
                name={name}
                title={title}
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
                onHover={hover}
                onSelect={(selection) =>
                  selection
                    ? navigate(jump(pages, { kind: "object", entity: selection.entity }), selection)
                    : setOutlined(false)
                }
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
              <Transport controls={session} view={view} disabled={!canChange} />
            )}
          </div>
          {tools && (
            <MediaToolsPanel
              {...panel}
              source={session}
              view={view}
              canSeek={canChange}
              selectable={objectDetails}
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
          jobs={session.exports}
          view={view}
          name={name}
          onClose={() => setExporting(null)}
        />
      )}
    </div>
  );
}
