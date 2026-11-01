import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useViewerActions } from "../viewer/actions";
import type { ViewerProps } from "../viewers";
import { PlaybackSession, type PlaybackView, type ResumeState } from "./session";
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
        <p className="m-0 text-small break-all text-on-canvas-muted">{view.failure.location}</p>
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

function Playback({ session, name }: { session: PlaybackSession; name: string }) {
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
          <VideoCanvas session={session} label={name} />
          <Transport controls={session} view={view} visible={!view.playing || moving || focused} />
        </>
      ) : (
        <Status view={view} name={name} />
      )}
    </div>
  );
}

/** Plays a video from the backend: the picture with its transport, or why there is none yet. */
export function VideoViewer({
  content,
  memory,
  connect = connectWorker,
}: ViewerProps<"SeekableVideo"> & { connect?: Connect }) {
  const [session, setSession] = useState<PlaybackSession | null>(null);
  useEffect(() => {
    const created = new PlaybackSession(content.id, {
      connect,
      resume: memory.saved as ResumeState | undefined,
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
      memory.save(created.resumeState());
      created.close();
    };
    // `memory.saved` is only read when the session starts; a new `memory` object each render must not restart it.
  }, [content.id, connect]);
  return session && <Playback session={session} name={content.display_name} />;
}
