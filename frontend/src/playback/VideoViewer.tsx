import { LoadingIndicator } from "../kit/LoadingIndicator";
import { usePlaybackLink } from "./usePlaybackLink";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ContentInfo, ContentRef, EntryInfo, LanePolicyWire } from "../generated/api";
import {
  useCan,
  useFollowing,
  useTakesPresenter,
  usePresenterChange,
  useOwnerSeat,
  useServerClock,
  useView,
} from "../session/session";
import { useSettings } from "../client/settings";
import { NOTHING_HIDDEN, type Hidden } from "../viewer/hidden";
import { usePaneBudget } from "./budget";
import { connectWorker } from "../stream/connect";
import type { LaneSettings } from "../lanes/contract";
import { FRESH_MEMORY, type InspectorMemory } from "../inspect/fields";
import { savedDefaults } from "../lanes/lanes";
import { Playback } from "./PlaybackPane";
import { Status } from "./PlaybackStatus";
import { leave, take } from "./reloadHandoff";
import type { PreparedEntry } from "./nextEntry";
import { PlaybackStream } from "./stream";
import type { PlaybackIntent, ResumeState } from "./streamDeps";
import { useFileScopeOwner } from "../inspect/useFileScopeOwner";
import { useSharedViewport } from "./sharedViewport";
import type { Connect } from "../stream/wire";

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
  /** The entry the playlist steps to from this one, with the lanes it would show; absent when there is none. */
  next?: PreparedEntry | null;
  /** Hides one of the entry's lanes by its label; absent when this seat may not change the workspace. */
  onHideLane?: (label: string) => void;
  /** Keeps the matching of one of the entry's lanes, by its slot, in the workspace; absent when this seat may not. */
  onLaneMatching?: (slot: string, policy: LanePolicyWire) => void;
  connect?: Connect;
};

/** What a clock intent sets, apart from the time it was stamped. */

/**
 * Plays one entry from the backend: its lanes in a grid with the transport over their bottom edge, or why there is no picture
 * yet. What the clock does is the window's view of this item, for the entry that view names. Whatever the viewer does
 * with the controls is published when the seat may change its view, also while following: that is what makes the
 * backend let the seat explore. The presenter's clock drives a following window and is never published back; an Owner
 * seat follows a clock another Owner seat set, and is the one that sets it when it acts. Lane
 * settings (catalog, classes, sticky) belong to this window's own stream and are not shared.
 */
export function VideoViewer({
  item,
  viewer,
  content,
  entry,
  hidden = NOTHING_HIDDEN,
  next,
  onHideLane,
  onLaneMatching,
  connect = connectWorker,
}: VideoViewerProps) {
  const key = useMemo<ContentRef>(
    () => ({ content: content.id, generation: content.generation, entry: entry.id, viewer }),
    [content.id, content.generation, entry.id, viewer],
  );
  const { ticket, connect: trackedConnect } = usePlaybackLink(key, connect);
  const [viewers, setViewers] = useView("viewers");
  // A clock belongs to the entry the view names beside it; one left from another entry is not this viewer's.
  const shown = viewers[viewer];
  const intent = shown?.entry === undefined || shown.entry === entry.id ? shown?.clock : undefined;
  const canChange = useCan("view");
  const following = useFollowing();
  const ownerSeat = useOwnerSeat();
  const clock = useServerClock();
  const presenter = usePresenterChange();
  const takesPresenter = useTakesPresenter();
  // The stream and Presenter View revision this window last took the clock from.
  const adopted = useRef<{ stream: PlaybackStream; rev: number } | undefined>(undefined);
  const [stream, setStream] = useState<PlaybackStream | null>(null);
  // How the pictures are zoomed belongs to the viewer, so it stays across entries and reconnections; the Presenter View
  // shares it.
  const { viewport, onViewport, onDrawn } = useSharedViewport(viewer);
  // The speed belongs to the viewer too: the entry that opens next plays at the speed the last one left.
  const heldSpeed = useRef<number | undefined>(undefined);
  // Pinned fields and folded groups belong to the viewer too: they follow the user from object to object.
  const [inspector, setInspector] = useState<InspectorMemory>(FRESH_MEMORY);
  const { values } = useSettings();
  // The lane settings of the entry that closed, which the next entry's lanes start from.
  const priorLanes = useRef<readonly LaneSettings[]>([]);
  // The effect that creates the stream reads these as they are then, and must not restart when they change.
  const latest = useRef({ intent, canChange, following, clock, onLaneMatching });
  latest.current = { intent, canChange, following, clock, onLaneMatching };
  const defaultCatalog = useRef(values["overlays.default_catalog"] as string);
  defaultCatalog.current = values["overlays.default_catalog"] as string;
  const saved = useRef(savedDefaults(values));
  saved.current = savedDefaults(values);
  // Where the entry was left, for the stream that replaces the one that closed on the same entry. The published intent
  // still names the last anchor when the replacement starts, so reading it there would reopen at the frame playback
  // began from. Another entry starts over: only the speed goes with the viewer.
  const left = useRef<{ entry: string; state: ResumeState } | undefined>(undefined);
  // The key of the entry whose autoplay a replaced stream lost before anything was shown: the replacement plays.
  const interrupted = useRef<string | undefined>(undefined);

  // Whether this viewer is going away, as against starting a stream over. Declared before the effect below, whose
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
    const anchor = latest.current.intent;
    // A window that follows takes the presenter's clock, never a position of its own, and forgets any it had.
    const kept = left.current;
    left.current = undefined;
    const carried = !latest.current.following && kept?.entry === entry.id ? kept.state : undefined;
    const streamKey = `${item}/${entry.id}`;
    const retried = interrupted.current === streamKey;
    interrupted.current = undefined;
    // The viewer a reload replaced hands over where it was and whether it played: the reloaded one plays on.
    const handed = latest.current.following
      ? undefined
      : take(viewer, content.id, content.generation, entry.id);
    const start = handed ?? {
      // Frame 0 of a replaced stream that showed nothing is no position to come back to.
      frame: retried ? undefined : (carried?.frame ?? anchor?.frame),
      speed: heldSpeed.current ?? carried?.speed ?? (anchor && anchor.speedCenti / 100),
    };
    // A window that reloads and takes its seat back finds its clock still running in the accepted intent, which
    // the Guests keep following: it picks the clock up where it is by now, as a following window does.
    const running =
      !handed && !carried && !latest.current.following && anchor?.playing ? anchor : undefined;
    const created = new PlaybackStream({
      ticket,
      connect: trackedConnect,
      resume: start,
      // An open with no position to come back to plays; a resumed tab or a followed clock stays as it is. A seat that
      // cannot publish its clock would play again on every return, as nothing remembers it was left.
      autoplay: handed
        ? handed.playing
        : start.frame === undefined && !latest.current.following && latest.current.canChange,
      lanes: priorLanes.current,
      defaultCatalog: () => defaultCatalog.current || null,
      savedDefaults: () => saved.current,
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
    setStream(created);
    return () => {
      // A stream that replaces this one on the same entry opens where it was left, paused.
      const { frame, speed, playing } = created.resumeState();
      if (created.autoplayPending) interrupted.current = streamKey;
      const lanes = created.laneSettings();
      if (lanes.length > 0) priorLanes.current = lanes;
      created.close();
      // A window that follows has not chosen anything, so leaving does not make it explore.
      if (!latest.current.following) {
        publish({ frame, playing: false, speed });
        // A viewer that goes away may be replaced by a reload of its Content, which plays on if it was playing.
        if (going.current)
          leave(viewer, {
            content: content.id,
            generation: content.generation,
            entry: entry.id,
            frame,
            speed,
            playing,
          });
        left.current = { entry: entry.id, state: { frame, speed } };
        heldSpeed.current = speed;
      }
    };
  }, [item, viewer, content.id, content.generation, entry.id, ticket, trackedConnect, setViewers]);

  // The share of the window's frame memory follows the panes that are open.
  const budget = usePaneBudget();
  useEffect(() => stream?.setBudget(budget), [stream, budget]);

  // The backend readies the entry that comes next while this one plays, when its settings allow.
  useEffect(() => stream?.next.ask(next ?? null), [stream, next]);

  useFileScopeOwner(stream, following);
  useEffect(() => {
    if (stream && following && intent) stream.follow(intent, () => clock.nowUs() ?? intent.atUs);
  }, [stream, following, intent, clock]);
  // Every Owner seat shows the one Presenter View, so a change another Owner seat made is this window's too, until the
  // user acts here. The server says who made each change; this window's own come back as its own and are not applied.
  // The window's first look at a viewer in its Session adopts the view whoever made it, and a change of its own that
  // the backend has not settled yet is never overwritten.
  useEffect(() => {
    if (!stream || !ownerSeat) return;
    const takes = takesPresenter(viewer, presenter.byOther);
    const last = adopted.current;
    if (!intent || (last?.stream === stream && last.rev === presenter.rev)) return;
    adopted.current = { stream, rev: presenter.rev };
    if (takes) stream.follow(intent, () => clock.nowUs() ?? intent.atUs, true);
  }, [stream, ownerSeat, intent, clock, viewer, takesPresenter, presenter.rev, presenter.byOther]);

  return stream ? (
    <Playback
      stream={stream}
      viewer={viewer}
      item={item}
      name={entry.label}
      title={content.display_name}
      entry={entry.id}
      hidden={hidden}
      canChange={canChange}
      onHideLane={onHideLane}
      viewport={viewport}
      onViewport={onViewport}
      onDrawn={onDrawn}
      inspector={inspector}
      onInspector={setInspector}
    />
  ) : (
    <LoadingIndicator message={`Opening ${entry.label}…`} />
  );
}
