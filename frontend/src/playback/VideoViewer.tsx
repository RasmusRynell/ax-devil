import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ContentInfo, ContentRef, EntryInfo, LanePolicyWire } from "../generated/api";
import { ExportDialog } from "../export/ExportDialog";
import { choicesOf } from "../export/lanes";
import { ContextMenu } from "../kit/ContextMenu";
import { Icon } from "../kit/Icon";
import { useSettings } from "../settings";
import {
  useAllowed,
  useCan,
  useFollowing,
  useServerClock,
  useStream,
  useView,
} from "../session/session";
import { useViewerActions } from "../viewer/actions";
import { NOTHING_HIDDEN, type Hidden } from "../viewer/hidden";
import { NoticeList } from "../kit/NoticeList";
import { useChromeVisibility } from "./chromeVisibility";
import { LaneGrid } from "./LaneGrid";
import { HIDE_LANE, laneContextEntries } from "./laneContextMenu";
import { shownLanes } from "./laneCells";
import { useLaneFullscreen } from "./laneFullscreen";
import { Popover } from "../kit/Popover";
import { ObjectsPanel } from "../tools/ObjectsPanel";
import { VIDEO_LEAST } from "../tools/panelWidth";
import type { LaneSettings, Sticky, Viewport } from "./contract";
import { FRESH_MEMORY, type InspectorMemory } from "./fields";
import { Inspector } from "./Inspector";
import { ObjectLayer, type ObjectLayerHandle } from "./ObjectLayer";
import type { Geometry } from "./renderer";
import { PlaybackSession } from "./session";
import type { PlaybackIntent, ResumeState } from "./sessionDeps";
import { connectWorker } from "./connect";
import { Transport } from "./Transport";
import type { PlaybackView } from "./view";
import { FIT, ZOOM_STEP } from "./viewport";
import type { Connect } from "./wire";
import { useFileScopeOwner } from "./useFileScopeOwner";

/** What the pane says while the video is not ready: indexing with its progress, reconnecting, or why it failed. */
function Status({ view, name }: { view: PlaybackView; name: string }) {
  if (view.status === "failed" && view.failure)
    return (
      <div role="alert" className="m-auto flex max-w-full flex-col gap-s p-xl text-center">
        <p className="m-0 font-semibold break-words">{view.failure.reason}</p>
        {view.failure.location && (
          <p className="m-0 text-small break-all text-on-canvas-muted">{view.failure.location}</p>
        )}
      </div>
    );
  const text =
    view.status === "indexing"
      ? `Indexing ${name}… ${Math.round(view.progress * 100)}%`
      : view.status === "reconnecting"
        ? "Connection lost. Reconnecting…"
        : `Opening ${name}…`;
  return (
    <div
      role="status"
      aria-busy="true"
      className="m-auto flex w-full max-w-md flex-col gap-m p-xl text-center"
    >
      <p className="m-0">{text}</p>
      {view.status === "indexing" && (
        <div
          role="progressbar"
          aria-label="Indexing progress"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={view.progress}
          className="h-s w-full overflow-hidden rounded-control bg-canvas-hover"
        >
          <div className="h-full bg-on-canvas" style={{ width: `${view.progress * 100}%` }} />
        </div>
      )}
    </div>
  );
}

function Playback({
  session,
  item,
  name,
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
  const [tools, setTools] = useState(false);
  // The width the user gave the media tools, kept while they are closed.
  const [toolsWidth, setToolsWidth] = useState<number | null>(null);
  // One switch for every lane; it lives here so it stays across entries.
  const [info, setInfo] = useState(false);
  useChromeVisibility(pane);
  const shown = useMemo(() => shownLanes(view.lanes, hidden, entry), [view.lanes, hidden, entry]);
  const fullscreen = useLaneFullscreen(pane, shown);
  const shownViewport = useRef(viewport);
  shownViewport.current = viewport;
  // A viewer that opens takes the keyboard, so its shortcuts work at once.
  useEffect(() => pane.current?.focus({ preventScroll: true }), []);
  // Pointing at objects is switched off: nothing stays selected.
  useEffect(() => {
    if (!objectDetails) session.select(null);
  }, [session, objectDetails]);
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
  const exportLane = useCallback(
    (lane: number | null) => {
      if (session.getSnapshot().status !== "ready") return;
      session.pause();
      setExporting({ lane });
    },
    [session],
  );
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
      }),
      [session, zoom, onViewport, fullscreen.toggle, exportLane],
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
          if (!fullscreen.leave()) session.select(null);
      }}
    >
      {view.status === "ready" ? (
        <div className="flex min-h-0 flex-1">
          <div
            data-chrome-idle=""
            // The columns beside the video give way before it gets narrower than this.
            style={{ minWidth: tools ? VIDEO_LEAST : 0 }}
            className="relative min-h-0 flex-1"
          >
            <ContextMenu
              className="absolute inset-0"
              entries={() => {
                const lane = fullscreen.target();
                const cold = choicesOf(view.lanes).find((choice) => choice.lane === lane)?.cold;
                return laneContextEntries(allowed, shortcuts, onHideLane !== undefined, !!cold);
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
                anchor={`frame:${item}`}
                entry={entry}
                hidden={hidden}
                only={fullscreen.solo}
                info={info}
                onHide={onHideLane}
                onPoint={fullscreen.point}
                onPick={fullscreen.pick}
                viewport={viewport}
                onViewport={onViewport}
                objectDetails={objectDetails}
                onHover={hover}
                onSelect={(selection) => session.select(selection)}
                onDrawn={drawn}
              />
            </ContextMenu>
            <ObjectLayer
              selection={view.selection}
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
            <Transport controls={session} view={view} disabled={!canChange} />
          </div>
          {tools && (
            <ObjectsPanel
              source={session}
              view={view}
              canSeek={canChange}
              selectable={objectDetails}
              width={toolsWidth}
              onWidth={setToolsWidth}
              onClose={() => setTools(false)}
            />
          )}
          {objectDetails && view.inspection.state !== "none" && (
            <Inspector
              inspection={view.inspection}
              onClose={() => session.select(null)}
              crowded={tools}
              memory={inspector}
              onMemory={onInspector}
            />
          )}
        </div>
      ) : (
        <Status view={view} name={name} />
      )}
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

/** Where each entry of an item was left, by entry id: kept by whoever shows the entries, for as long as it does. */
export type EntryMemory = Map<string, ResumeState>;

/** What the viewer is given besides the item: the Content to stream from and the entry of it to play. */
export type VideoViewerProps = {
  /** The workspace item whose viewer this is; the window's view keeps this viewer's clock under it. */
  item: string;
  /** The Content the stream belongs to, a video or a playlist. */
  content: Pick<ContentInfo, "id" | "generation" | "display_name">;
  /** The entry to play. A stream is for one entry, and the viewer starts over when it changes. */
  entry: EntryInfo;
  /** What the user hid in the workspace: lanes left out of the grid. */
  hidden?: Hidden;
  /** Hides one of the entry's lanes by its label; absent when this seat may not change the workspace. */
  onHideLane?: (label: string) => void;
  /** Keeps the matching of one of the entry's lanes, by its slot, in the workspace; absent when this seat may not. */
  onLaneMatching?: (slot: string, policy: LanePolicyWire) => void;
  /**
   * Where the entries this viewer was shown for were left, so that coming back to one reopens it there. A viewer
   * shown for one entry needs none.
   */
  memory?: EntryMemory;
  connect?: Connect;
};

/**
 * Plays one entry from the backend: its lanes in a grid with the transport over their bottom edge, or why there is no picture
 * yet. What the clock does is the window's view of this item, for the entry that view names. Whatever the viewer does
 * with the controls is published when the seat may change its view, also while following: that is what makes the
 * backend let the seat explore. The presenter's clock drives a following window and is never published back. Lane
 * settings (catalog, classes, sticky) belong to this window's own stream and are not shared.
 */
export function VideoViewer({
  item,
  content,
  entry,
  hidden = NOTHING_HIDDEN,
  onHideLane,
  onLaneMatching,
  memory,
  connect = connectWorker,
}: VideoViewerProps) {
  const key = useMemo<ContentRef>(
    () => ({ content: content.id, generation: content.generation, entry: entry.id }),
    [content.id, content.generation, entry.id],
  );
  const ticket = useStream(key);
  const [viewers, setViewers] = useView("viewers");
  // A clock belongs to the entry the view names beside it; one left from another entry is not this viewer's.
  const shown = viewers[item];
  const intent = shown?.entry === undefined || shown.entry === entry.id ? shown?.clock : undefined;
  const canChange = useCan("view");
  const following = useFollowing();
  const clock = useServerClock();
  const [session, setSession] = useState<PlaybackSession | null>(null);
  // How the pictures are zoomed belongs to the viewer, so it stays across entries and reconnections.
  const [viewport, setViewport] = useState<Viewport>(FIT);
  // The speed belongs to the viewer too: the entry that opens next plays at the speed the last one left.
  const heldSpeed = useRef<number | undefined>(undefined);
  // Pinned fields and folded groups belong to the viewer too: they follow the user from object to object.
  const [inspector, setInspector] = useState<InspectorMemory>(FRESH_MEMORY);
  const { values } = useSettings();
  // The lane settings of the entry that closed, which the next entry's lanes start from.
  const priorLanes = useRef<readonly LaneSettings[]>([]);
  // The effect that creates the session reads these as they are then, and must not restart when they change.
  const latest = useRef({ intent, canChange, following, clock, onLaneMatching });
  latest.current = { intent, canChange, following, clock, onLaneMatching };
  const defaultCatalog = useRef(values["overlays.default_catalog"] as string);
  defaultCatalog.current = values["overlays.default_catalog"] as string;
  const defaultSticky = useRef(values["overlays.default_sticky"] as Sticky);
  defaultSticky.current = values["overlays.default_sticky"] as Sticky;
  // Where each entry was left, for the session that replaces the one that closed. The published intent still names the
  // last anchor when the replacement starts, so reading it there would reopen at the frame playback began from.
  const own = useRef<EntryMemory>(new Map());
  const left = memory ?? own.current;

  useEffect(() => {
    const publish = ({ frame, playing, speed }: PlaybackIntent) => {
      const { canChange: allowed, clock: server } = latest.current;
      if (!allowed) return;
      // A clock that is not in step yet stamps the future, which the backend cuts back to its own time. The wire
      // carries whole microseconds.
      const atUs = Math.round(server.nowUs() ?? Number.MAX_SAFE_INTEGER);
      const next = { frame, playing, speedCenti: Math.round(speed * 100), atUs };
      // The view may already name another entry, which the viewer was left for: its clock is not this one's.
      setViewers((now) => {
        const current = now[item];
        if (current?.entry !== undefined && current.entry !== entry.id) return now;
        return { ...now, [item]: { ...current, clock: next } };
      });
    };
    const anchor = latest.current.intent;
    // A window that follows takes the presenter's clock, never a position of its own, and forgets any it had.
    const carried = latest.current.following ? undefined : left.get(entry.id);
    left.delete(entry.id);
    const start = {
      frame: carried?.frame ?? anchor?.frame,
      speed: heldSpeed.current ?? carried?.speed ?? (anchor && anchor.speedCenti / 100),
    };
    // A window that reloads and takes its seat back finds its clock still running in the accepted intent, which
    // the watchers keep following: it picks the clock up where it is by now, as a following window does.
    const running = !carried && !latest.current.following && anchor?.playing ? anchor : undefined;
    const created = new PlaybackSession({
      ticket,
      connect,
      resume: start,
      lanes: priorLanes.current,
      defaultCatalog: () => defaultCatalog.current || null,
      defaultSticky: () => defaultSticky.current,
      onIntent: publish,
      onPolicy: (slot, policy) => latest.current.onLaneMatching?.(slot, policy),
      now: () => performance.now(),
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
    if (running) created.follow(running, () => latest.current.clock.nowUs() ?? running.atUs);
    setSession(created);
    return () => {
      // Shown again later, the viewer opens where it was left, paused.
      const { frame, speed } = created.resumeState();
      const lanes = created.laneSettings();
      if (lanes.length > 0) priorLanes.current = lanes;
      created.close();
      // A window that follows has not chosen anything, so leaving does not make it explore.
      if (!latest.current.following) {
        publish({ frame, playing: false, speed });
        left.set(entry.id, { frame, speed });
        heldSpeed.current = speed;
      }
    };
  }, [item, entry.id, ticket, connect, setViewers, left]);

  useFileScopeOwner(session, following);
  useEffect(() => {
    if (session && following && intent) session.follow(intent, () => clock.nowUs() ?? intent.atUs);
  }, [session, following, intent, clock]);

  return (
    session && (
      <Playback
        session={session}
        item={item}
        name={entry.label}
        entry={entry.id}
        hidden={hidden}
        canChange={canChange}
        onHideLane={onHideLane}
        viewport={viewport}
        onViewport={setViewport}
        inspector={inspector}
        onInspector={setInspector}
      />
    )
  );
}
