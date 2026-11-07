import { useEffect, useMemo, useRef, useState } from "react";
import type { ContentInfo, ContentRef, EntryInfo, LanePolicyWire } from "../generated/api";
import {
  useCan,
  useFollowing,
  useReadView,
  useServerClock,
  useStream,
  useView,
} from "../session/session";
import { useSettings } from "../settings";
import { NOTHING_HIDDEN, type Hidden } from "../viewer/hidden";
import { usePaneBudget } from "./budget";
import { connectWorker } from "./connect";
import type { LaneSettings, Viewport } from "./contract";
import { FRESH_MEMORY, type InspectorMemory } from "./fields";
import { savedTiming } from "./lanes";
import { Playback } from "./PlaybackPane";
import { Status } from "./PlaybackStatus";
import { PlaybackSession } from "./session";
import type { PlaybackIntent, ResumeState } from "./sessionDeps";
import { useFileScopeOwner } from "./useFileScopeOwner";
import { FIT } from "./viewport";
import type { Connect } from "./wire";

export { Status };

/** What the viewer is given besides the item: the Content to stream from and the entry of it to play. */
export type VideoViewerProps = {
  /** The workspace item whose viewer this is. */
  item: string;
  /** This viewer's id; the window's view keeps its clock and entry under it. */
  viewer: number;
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
  viewer,
  content,
  entry,
  hidden = NOTHING_HIDDEN,
  onHideLane,
  onLaneMatching,
  connect = connectWorker,
}: VideoViewerProps) {
  const key = useMemo<ContentRef>(
    () => ({ content: content.id, generation: content.generation, entry: entry.id, viewer }),
    [content.id, content.generation, entry.id, viewer],
  );
  const ticket = useStream(key);
  const [viewers, setViewers] = useView("viewers");
  const readView = useReadView(); // A clock belongs to the entry the view names beside it; one left from another entry is not this viewer's.
  const shown = viewers[viewer];
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
  const latest = useRef({ canChange, following, clock, onLaneMatching });
  latest.current = { canChange, following, clock, onLaneMatching };
  const defaultCatalog = useRef(values["overlays.default_catalog"] as string);
  defaultCatalog.current = values["overlays.default_catalog"] as string;
  const defaultTiming = useRef(savedTiming(values));
  defaultTiming.current = savedTiming(values);
  // Where the entry was left, for the session that replaces the one that closed on the same entry. The published intent
  // still names the last anchor when the replacement starts, so reading it there would reopen at the frame playback
  // began from. Another entry starts over: only the speed goes with the viewer.
  const left = useRef<{ entry: string; state: ResumeState } | undefined>(undefined);
  // The stream whose autoplay a session replacement interrupted before anything was shown: the replacement plays.
  const interrupted = useRef<string | undefined>(undefined);

  // Whether this viewer is going away, as against starting a session over. Declared before the effect below, whose
  // cleanup reads it, so it is set first when the viewer unmounts.
  const going = useRef(false);
  useEffect(() => {
    going.current = false;
    return () => {
      going.current = true;
    };
  }, []);

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
        const current = now[viewer];
        if (!current || (current.entry !== undefined && current.entry !== entry.id)) return now;
        return { ...now, [viewer]: { ...current, clock: next } };
      });
    };
    // Read from the view as it is now, not as the last render saw it: a viewer this one replaces on a reload has just
    // published its clock as it was, and the render's intent is from before that.
    const named = readView().viewers[viewer];
    const anchor =
      named?.entry === undefined || named.entry === entry.id ? named?.clock : undefined;
    // A window that follows takes the presenter's clock, never a position of its own, and forgets any it had.
    const kept = left.current;
    left.current = undefined;
    const carried = !latest.current.following && kept?.entry === entry.id ? kept.state : undefined;
    const streamKey = `${item}/${entry.id}`;
    const retried = interrupted.current === streamKey;
    interrupted.current = undefined;
    const start = {
      // Frame 0 of a replaced session that showed nothing is no position to come back to.
      frame: retried ? undefined : (carried?.frame ?? anchor?.frame),
      speed: heldSpeed.current ?? carried?.speed ?? (anchor && anchor.speedCenti / 100),
    };
    // A window that reloads and takes its seat back finds its clock still running in the accepted intent, which
    // the watchers keep following: it picks the clock up where it is by now, and stays the one that publishes it.
    const running = !carried && !latest.current.following && anchor?.playing ? anchor : undefined;
    const created = new PlaybackSession({
      ticket,
      connect,
      resume: start,
      // An open with no position to come back to plays; a resumed tab or a followed clock stays as it is. A seat that
      // cannot publish its clock would play again on every return, as nothing remembers it was left.
      autoplay: start.frame === undefined && !latest.current.following && latest.current.canChange,
      lanes: priorLanes.current,
      defaultCatalog: () => defaultCatalog.current || null,
      defaultTiming: () => defaultTiming.current,
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
    if (running) created.restore(running, () => latest.current.clock.nowUs() ?? running.atUs);
    setSession(created);
    return () => {
      // A session that replaces this one on the same entry opens where it was left, paused. A viewer that goes away
      // leaves its clock as it is: a reload mounts the next viewer, which carries on from that playhead.
      const { frame, speed } = created.resumeState();
      const playing = going.current && created.getSnapshot().playing;
      if (created.autoplayPending) interrupted.current = streamKey;
      const lanes = created.laneSettings();
      if (lanes.length > 0) priorLanes.current = lanes;
      created.close();
      // A window that follows has not chosen anything, so leaving does not make it explore.
      if (!latest.current.following) {
        publish({ frame, playing, speed });
        left.current = { entry: entry.id, state: { frame, speed } };
        heldSpeed.current = speed;
      }
    };
  }, [item, viewer, entry.id, ticket, connect, setViewers, readView]);

  // The share of the window's frame memory follows the panes that are open.
  const budget = usePaneBudget();
  useEffect(() => session?.setBudget(budget), [session, budget]);

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
        title={content.display_name}
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
