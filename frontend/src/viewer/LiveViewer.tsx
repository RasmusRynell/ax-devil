import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import type { ContentRef, LanePolicyWire } from "../generated/api";
import { LiveBar, NoPicture } from "../live/LiveBar";
import { LiveSession } from "../live/session";
import { useReportStatus } from "../live/streamState";
import { ContextMenu } from "../kit/ContextMenu";
import type { LaneSettings } from "../lanes/contract";
import { connectWorker } from "../stream/connect";
import { FRESH_MEMORY, type InspectorMemory } from "../inspect/fields";
import { shownLanes } from "../playback/laneCells";
import { savedDefaults } from "../lanes/lanes";
import { LIVE_LANE_COMMANDS, laneContextEntries } from "../lanes/laneContextMenu";
import { useLaneFullscreen } from "../playback/laneFullscreen";
import { LaneGrid } from "../playback/LaneGrid";
import { ObjectLayer, type ObjectLayerHandle } from "../playback/ObjectLayer";
import type { Geometry } from "../playback/renderer";
import { Status } from "../playback/VideoViewer";
import { useSharedViewport } from "../playback/sharedViewport";
import { FIT, ZOOM_STEP } from "../playback/viewport";
import type { Connect } from "../stream/wire";
import {
  useAllowed,
  useCan,
  useCommand,
  useFollowing,
  useOverlays,
  useStream,
} from "../session/session";
import { useSettings } from "../client/settings";
import { MediaToolsPanel } from "../tools/MediaToolsPanel";
import { useMediaTools } from "../tools/useMediaTools";
import type { ViewerProps } from "../viewers";
import { PaneActiveContext, useViewerActions } from "./actions";
import { NOTHING_HIDDEN } from "./hidden";

function Live({
  viewer,
  session,
  item,
  entry,
  name,
}: {
  session: LiveSession;
  viewer: number;
  item: string;
  /** The Content's id: a live video's one entry. */
  entry: string;
  name: string;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useReportStatus(viewer, item, view);
  const { shortcuts } = useSettings();
  const { objectDetails, laneNames } = useOverlays();
  const allowed = useAllowed();
  const following = useFollowing();
  const pane = useRef<HTMLDivElement>(null);
  const objects = useRef<ObjectLayerHandle>(null);
  const geometry = useRef<Geometry | null>(null);
  const [tools, setTools] = useState(false);
  const { viewport, onViewport, onDrawn } = useSharedViewport(viewer);
  const [inspector, setInspector] = useState<InspectorMemory>(FRESH_MEMORY);
  // The media tools' pages, lists and width, kept while they are closed; the selected object is the page on top.
  const { outlined, pick, chooseLane, panel } = useMediaTools(session, objectDetails, tools);
  const shown = useMemo(() => shownLanes(view.lanes, NOTHING_HIDDEN, entry), [view.lanes, entry]);
  const fullscreen = useLaneFullscreen(pane, shown);
  const shownViewport = useRef(viewport);
  shownViewport.current = viewport;
  const controllable = !following && allowed.view;
  // Following a presenter ends a pause of this window's own: the backend shows it the stream again.
  useEffect(() => {
    if (following) session.follow();
  }, [session, following]);
  // A viewer that opens in the active pane takes the keyboard, so its shortcuts work at once; one that opens behind it
  // leaves the keyboard and the active pane alone.
  const opensInFront = useRef(useContext(PaneActiveContext));
  useEffect(() => {
    if (opensInFront.current) pane.current?.focus({ preventScroll: true });
  }, []);
  // The selected object's page is what the media tools show, so its fields are asked for only while they are open.
  useEffect(() => session.showInspector(tools), [session, tools]);
  const zoom = useCallback(
    (factor: number) => {
      const next = geometry.current?.zoomAt(shownViewport.current, factor);
      if (!onViewport || !next || next === shownViewport.current) return;
      shownViewport.current = next;
      onViewport(next);
    },
    [onViewport],
  );
  const togglePause = useCallback(() => {
    if (!controllable) return;
    if (session.getSnapshot().paused) session.resume();
    else session.pause();
  }, [session, controllable]);
  const actions = useViewerActions(
    useMemo(
      () => ({
        "zoom-in": () => zoom(ZOOM_STEP),
        "zoom-out": () => zoom(1 / ZOOM_STEP),
        "zoom-reset": () => onViewport?.(FIT),
        "play-pause": togglePause,
        "media-tools": () => {
          if (!tools) {
            const lane = fullscreen.target();
            if (lane !== null) chooseLane(lane);
          }
          setTools((open) => !open);
        },
        "lane-fullscreen": fullscreen.toggle,
      }),
      [zoom, onViewport, togglePause, fullscreen.toggle, fullscreen.target, tools, chooseLane],
    ),
  );
  return (
    <div
      ref={pane}
      tabIndex={-1}
      data-popover-bounds=""
      className="relative flex min-h-0 flex-1 flex-col bg-canvas text-on-canvas outline-none @container"
      onKeyDown={(event) => {
        // An Escape a popup closed is spent, and in fullscreen Escape only leaves it.
        const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
        if (event.key === "Escape" && plain && !event.nativeEvent.defaultPrevented)
          if (!fullscreen.leave()) session.select(null);
      }}
    >
      <LiveBar
        view={view}
        controls={session}
        disabled={!controllable}
        tools={tools}
        onTools={setTools}
      />
      {view.status === "ready" ? (
        <div className="flex min-h-0 flex-1">
          <div className="relative min-h-0 min-w-0 flex-1">
            <ContextMenu
              className="absolute inset-0"
              entries={laneContextEntries(LIVE_LANE_COMMANDS, allowed, shortcuts, false, false)}
              onSelect={({ id }) => {
                actions[id]?.();
              }}
            >
              <LaneGrid
                stream={session}
                view={view}
                name={name}
                anchor={`frame:${item}`}
                entry={entry}
                hidden={NOTHING_HIDDEN}
                only={fullscreen.solo}
                live
                onPoint={fullscreen.point}
                onPick={fullscreen.pick}
                viewport={viewport}
                onViewport={onViewport}
                objectDetails={objectDetails}
                laneNames={laneNames}
                onHover={(picked) => objects.current?.hover(picked)}
                onSelect={pick}
                onDrawn={(next) => {
                  geometry.current = next;
                  objects.current?.drawn(next);
                  onDrawn(next);
                }}
              />
            </ContextMenu>
            <ObjectLayer
              selection={outlined ? view.selection : null}
              objectDetails={objectDetails}
              handle={objects}
            />
            {!view.shown && <NoPicture video={view.video} />}
          </div>
          {tools && (
            <MediaToolsPanel
              {...panel}
              source={session}
              view={view}
              canSeek={false}
              selectable={objectDetails}
              log={session.events}
              memory={inspector}
              onMemory={setInspector}
              onClose={() => setTools(false)}
            />
          )}
        </div>
      ) : (
        <Status view={view} name={name} />
      )}
    </div>
  );
}

/**
 * Shows a live stream: the video with the overlay's lane, where each feed stands, and the Events list. The stream
 * starts when this viewer opens and ends for good when it closes ("Stop" is closing the viewer): nothing connects to
 * the camera before. The lane's matching is kept in the workspace by its slot, as a recording's is.
 */
export function LiveVideoViewer({
  item,
  viewer,
  content,
  connect = connectWorker,
}: ViewerProps<"LiveVideo"> & { connect?: Connect }) {
  const key = useMemo<ContentRef>(
    () => ({ content: content.id, generation: content.generation, entry: content.id, viewer }),
    [content.id, content.generation, viewer],
  );
  const ticket = useStream(key);
  const command = useCommand();
  const changes = useCan("content");
  const { values } = useSettings();
  const [session, setSession] = useState<LiveSession | null>(null);
  // The lane settings of the stream that closed, which the next one's lane starts from.
  const priorLanes = useRef<readonly LaneSettings[]>([]);
  const defaultCatalog = useRef(values["overlays.default_catalog"]);
  defaultCatalog.current = values["overlays.default_catalog"];
  const saved = useRef(savedDefaults(values));
  saved.current = savedDefaults(values);
  const keepMatching = useCallback(
    (slot: string, policy: LanePolicyWire) =>
      void command({
        type: "content",
        command: { type: "setLaneMatching", item, slot, policy },
      }).catch(() => undefined),
    [command, item],
  );
  // The effect that creates the session reads this as it is then, and must not restart when it changes.
  const onPolicy = useRef<typeof keepMatching | undefined>(undefined);
  onPolicy.current = changes ? keepMatching : undefined;

  useEffect(() => {
    const created = new LiveSession({
      ticket,
      connect,
      lanes: priorLanes.current,
      defaultCatalog: () => (defaultCatalog.current as string) || null,
      savedDefaults: () => saved.current,
      onPolicy: (slot, policy) => onPolicy.current?.(slot, policy),
      frame: (callback) => {
        const handle = requestAnimationFrame(callback);
        return () => cancelAnimationFrame(handle);
      },
      after: (callback, ms) => {
        const timer = setTimeout(callback, ms);
        return () => clearTimeout(timer);
      },
    });
    created.start();
    setSession(created);
    return () => {
      const lanes = created.laneSettings();
      if (lanes.length > 0) priorLanes.current = lanes;
      created.close();
    };
  }, [ticket, connect]);

  return (
    session && (
      <Live
        session={session}
        viewer={viewer}
        item={item}
        entry={content.id}
        name={content.display_name}
      />
    )
  );
}
