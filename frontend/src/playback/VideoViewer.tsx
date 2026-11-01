import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { StreamKey } from "../generated/api";
import { useCan, useFollowing, useServerClock, useStream, useView } from "../session/session";
import { useViewerActions } from "../viewer/actions";
import type { ViewerProps } from "../viewers";
import { PlaybackSession, type PlaybackIntent, type PlaybackView } from "./session";
import { connectWorker } from "./connect";
import { Transport } from "./Transport";
import { VideoCanvas } from "./VideoCanvas";
import type { Connect } from "./wire";

/** How long the controls stay over a playing video after the pointer last moved. */
const CONTROLS_IDLE_MS = 2000;

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
  canChange,
}: {
  session: PlaybackSession;
  item: string;
  name: string;
  canChange: boolean;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [moving, setMoving] = useState(true);
  const [focused, setFocused] = useState(false);
  const pane = useRef<HTMLDivElement>(null);
  // A viewer that opens takes the keyboard, so its shortcuts work at once.
  useEffect(() => pane.current?.focus({ preventScroll: true }), []);
  useEffect(() => {
    if (!moving) return;
    const timer = setTimeout(() => setMoving(false), CONTROLS_IDLE_MS);
    return () => clearTimeout(timer);
  }, [moving]);
  useViewerActions(
    useMemo(
      () => ({
        "play-pause": () => session.togglePlay(),
        "step-forward": () => session.step(1),
        "step-back": () => session.step(-1),
        "step-forward-10": () => session.step(10),
        "step-back-10": () => session.step(-10),
        "speed-up": () => session.stepSpeed(1),
        "speed-down": () => session.stepSpeed(-1),
      }),
      [session],
    ),
  );
  return (
    <div
      ref={pane}
      tabIndex={-1}
      onPointerMove={() => setMoving(true)}
      // Keyboard focus on a control keeps the controls up; the pane itself holding focus does not.
      onFocus={({ target, currentTarget }) =>
        setFocused(target !== currentTarget && target.matches(":focus-visible"))
      }
      onBlur={() => setFocused(false)}
      className="relative flex min-h-0 flex-1 flex-col bg-canvas text-on-canvas outline-none"
    >
      {view.status === "ready" ? (
        <>
          <VideoCanvas session={session} label={name} anchor={`frame:${item}`} />
          <Transport
            controls={session}
            view={view}
            visible={!view.playing || moving || focused}
            disabled={!canChange}
          />
        </>
      ) : (
        <Status view={view} name={name} />
      )}
    </div>
  );
}

/**
 * Plays a video from the backend: the picture with its transport, or why there is none yet. What the clock does is
 * the window's view of this item. Whatever the viewer does with the controls is published when the seat may change
 * its view, also while following: that is what makes the backend let the seat explore. The presenter's clock drives
 * a following window and is never published back.
 */
export function VideoViewer({
  item,
  content,
  connect = connectWorker,
}: ViewerProps<"SeekableVideo"> & { connect?: Connect }) {
  const key = useMemo<StreamKey>(
    () => ({ type: "frames", content: content.id, generation: content.generation }),
    [content.id, content.generation],
  );
  const ticket = useStream(key);
  const [viewers, setViewers] = useView("viewers");
  const intent = viewers[item]?.clock;
  const canChange = useCan("view");
  const following = useFollowing();
  const clock = useServerClock();
  const [session, setSession] = useState<PlaybackSession | null>(null);
  // The effect that creates the session reads these as they are then, and must not restart when they change.
  const latest = useRef({ intent, canChange, following, clock });
  latest.current = { intent, canChange, following, clock };

  useEffect(() => {
    const publish = ({ frame, playing, speed }: PlaybackIntent) => {
      const { canChange: allowed, clock: server } = latest.current;
      if (!allowed) return;
      // A clock that is not in step yet stamps the future, which the backend cuts back to its own time. The wire
      // carries whole microseconds.
      const atUs = Math.round(server.nowUs() ?? Number.MAX_SAFE_INTEGER);
      const next = { frame, playing, speedCenti: Math.round(speed * 100), atUs };
      setViewers((now) => ({ ...now, [item]: { ...now[item], clock: next } }));
    };
    const start = latest.current.intent;
    const created = new PlaybackSession({
      ticket,
      connect,
      resume: start && { frame: start.frame, speed: start.speedCenti / 100 },
      onIntent: publish,
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
    setSession(created);
    return () => {
      // Shown again later, the viewer opens where it was left, paused.
      const { frame, speed } = created.resumeState();
      created.close();
      // A window that follows has not chosen anything, so leaving does not make it explore.
      if (!latest.current.following) publish({ frame, playing: false, speed });
    };
  }, [item, ticket, connect, setViewers]);

  useEffect(() => {
    if (session && following && intent) session.follow(intent, () => clock.nowUs() ?? intent.atUs);
  }, [session, following, intent, clock]);

  return (
    session && (
      <Playback session={session} item={item} name={content.display_name} canChange={canChange} />
    )
  );
}
