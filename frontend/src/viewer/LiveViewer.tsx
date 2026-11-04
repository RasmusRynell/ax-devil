import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ContentRef, LanePolicyWire } from "../generated/api";
import { LiveBar, type LivePanel } from "../live/LiveBar";
import { EventsPanel } from "../live/EventsPanel";
import { LiveSession } from "../live/session";
import { useReportStatus } from "../live/streamState";
import { ContextMenu } from "../kit/ContextMenu";
import type { LaneSettings, Sticky, Viewport } from "../playback/contract";
import { connectWorker } from "../playback/connect";
import { FRESH_MEMORY, type InspectorMemory } from "../playback/fields";
import { shownLanes } from "../playback/laneCells";
import { HIDE_LANE, laneContextEntries } from "../playback/laneContextMenu";
import { useLaneFullscreen } from "../playback/laneFullscreen";
import { LaneGrid } from "../playback/LaneGrid";
import { ObjectLayer, type ObjectLayerHandle } from "../playback/ObjectLayer";
import type { Geometry } from "../playback/renderer";
import { Status } from "../playback/VideoViewer";
import { FIT, ZOOM_STEP } from "../playback/viewport";
import type { Connect } from "../playback/wire";
import { useAllowed, useCan, useCommand, useFollowing, useStream } from "../session/session";
import { useSettings } from "../settings";
import { ObjectsPanel, type Place } from "../tools/ObjectsPanel";
import { FRESH_LIST, type ListState } from "../tools/listState";
import { EMPTY, jump, topObject, type PageStack } from "../tools/pageStack";
import { VIDEO_LEAST } from "../tools/panelWidth";
import type { ViewerProps } from "../viewers";
import { useViewerActions } from "./actions";
import { useHidden } from "./useViewers";

/** The lane menu of a live viewer: what a recording's has, without exporting, which a stream cannot do. */
const exportable = (entry: { kind: string; id?: string }) => entry.id !== "export-video";

function Live({
  session,
  item,
  entry,
  name,
  canHide,
}: {
  session: LiveSession;
  item: string;
  /** The Content's id: a live video's one entry. */
  entry: string;
  name: string;
  canHide: boolean;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useReportStatus(item, view);
  const { values, shortcuts } = useSettings();
  const objectDetails = values["overlays.object_details"] === true;
  const allowed = useAllowed();
  const following = useFollowing();
  const command = useCommand();
  const hidden = useHidden();
  const pane = useRef<HTMLDivElement>(null);
  const objects = useRef<ObjectLayerHandle>(null);
  const geometry = useRef<Geometry | null>(null);
  const [panel, setPanel] = useState<LivePanel | null>(null);
  // The width the user gave each panel, kept while it is closed.
  const [widths, setWidths] = useState<Record<LivePanel, number | null>>({
    objects: null,
    events: null,
  });
  const [viewport, setViewport] = useState<Viewport>(FIT);
  const [inspector, setInspector] = useState<InspectorMemory>(FRESH_MEMORY);
  // The pages open in the objects panel and its list, kept while it is closed. The selected object is the one on top.
  const [pages, setPages] = useState<PageStack>(EMPTY);
  const [list, setList] = useState<ListState>(FRESH_LIST);
  // Whether the selected object is outlined on the picture: clearing the selection there does not close its page.
  const [outlined, setOutlined] = useState(true);
  const shown = useMemo(() => shownLanes(view.lanes, hidden, entry), [view.lanes, hidden, entry]);
  const fullscreen = useLaneFullscreen(pane, shown);
  const shownViewport = useRef(viewport);
  shownViewport.current = viewport;
  const controllable = !following && allowed.view;
  // Following a presenter ends a pause of this window's own: the backend shows it the stream again.
  useEffect(() => {
    if (following) session.follow();
  }, [session, following]);
  // A viewer that opens takes the keyboard, so its shortcuts work at once.
  useEffect(() => pane.current?.focus({ preventScroll: true }), []);
  // The selection and the pages move together, and a selection is the object of the page on top.
  const navigate = useCallback(
    (next: PageStack, at: Place) => {
      const object = topObject(next);
      setPages(next);
      setOutlined(true);
      session.select(object && { ...at, entity: object.entity });
    },
    [session],
  );
  // Pointing at objects is switched off: no page stays open, nothing stays selected.
  useEffect(() => {
    if (!objectDetails) {
      setPages(EMPTY);
      setOutlined(true);
      session.select(null);
    }
  }, [session, objectDetails]);
  // The selected object's page is what the objects panel shows, so its fields are asked for only while it is open.
  useEffect(() => session.showInspector(panel === "objects"), [session, panel]);
  const zoom = useCallback((factor: number) => {
    const next = geometry.current?.zoomAt(shownViewport.current, factor);
    if (!next || next === shownViewport.current) return;
    shownViewport.current = next;
    setViewport(next);
  }, []);
  const hideLane = useCallback(
    (label: string) =>
      // A refused hide leaves the lane on screen, which says so.
      void command({
        type: "content",
        command: { type: "setHidden", target: { entry, lane: label }, hidden: true },
      }).catch(() => undefined),
    [command, entry],
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
        "zoom-reset": () => setViewport(FIT),
        "play-pause": togglePause,
        "media-tools": () => setPanel((now) => (now === "objects" ? null : "objects")),
        "lane-fullscreen": fullscreen.toggle,
      }),
      [zoom, togglePause, fullscreen.toggle],
    ),
  );
  const setWidth = (which: LivePanel) => (width: number) =>
    setWidths((now) => ({ ...now, [which]: width }));
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
        panel={panel}
        onPanel={setPanel}
      />
      {view.status === "ready" ? (
        // Positioned, so that a panel that covers the viewer leaves the bar above it.
        <div className="relative flex min-h-0 flex-1">
          <div
            // The columns beside the video give way before it gets narrower than this.
            style={{ minWidth: panel ? VIDEO_LEAST : 0 }}
            className="relative min-h-0 flex-1"
          >
            <ContextMenu
              className="absolute inset-0"
              entries={laneContextEntries(allowed, shortcuts, canHide, false).filter(exportable)}
              onSelect={({ id }) => {
                const lane = fullscreen.target();
                if (id === HIDE_LANE) {
                  if (lane !== null) hideLane(view.lanes[lane].label);
                } else actions[id]?.();
              }}
            >
              <LaneGrid
                session={session}
                view={view}
                name={name}
                anchor={`frame:${item}`}
                entry={entry}
                hidden={hidden}
                only={fullscreen.solo}
                live
                onHide={canHide ? hideLane : undefined}
                onPoint={fullscreen.point}
                onPick={fullscreen.pick}
                viewport={viewport}
                onViewport={setViewport}
                objectDetails={objectDetails}
                onHover={(picked) => objects.current?.hover(picked)}
                onSelect={(selection) =>
                  selection
                    ? navigate(jump(pages, { kind: "object", entity: selection.entity }), selection)
                    : setOutlined(false)
                }
                onDrawn={(next) => {
                  geometry.current = next;
                  objects.current?.drawn(next);
                }}
              />
            </ContextMenu>
            <ObjectLayer
              selection={outlined ? view.selection : null}
              objectDetails={objectDetails}
              handle={objects}
            />
          </div>
          {panel === "objects" && (
            <ObjectsPanel
              source={session}
              view={view}
              canSeek={false}
              selectable={objectDetails}
              frameOnly
              pages={pages}
              onNavigate={navigate}
              list={list}
              onList={setList}
              memory={inspector}
              onMemory={setInspector}
              width={widths.objects}
              onWidth={setWidth("objects")}
              onClose={() => setPanel(null)}
            />
          )}
          {panel === "events" && (
            <EventsPanel
              log={session.events}
              width={widths.events}
              onWidth={setWidth("events")}
              onClose={() => setPanel(null)}
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
  content,
  connect = connectWorker,
}: ViewerProps<"LiveVideo"> & { connect?: Connect }) {
  const key = useMemo<ContentRef>(
    () => ({ content: content.id, generation: content.generation, entry: content.id }),
    [content.id, content.generation],
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
  const defaultSticky = useRef(values["overlays.default_sticky"] as Sticky);
  defaultSticky.current = values["overlays.default_sticky"] as Sticky;
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
      defaultSticky: () => defaultSticky.current,
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
        item={item}
        entry={content.id}
        name={content.display_name}
        canHide={changes}
      />
    )
  );
}
