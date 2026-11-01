import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { Popover } from "../kit/Popover";
import { useViewerActions } from "../viewer/actions";
import type { Hidden } from "../viewer/hidden";
import { setHidden } from "../viewer/requests";
import type { ViewerProps } from "../viewers";
import { NoticeList } from "./LaneChip";
import { LaneGrid } from "./LaneGrid";
import { PlaybackSession, type PlaybackView, type ResumeState } from "./session";
import { connectWorker } from "./connect";
import { Transport } from "./Transport";
import type { Connect } from "./wire";

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

function Playback({
  session,
  name,
  entry,
  hidden,
}: {
  session: PlaybackSession;
  name: string;
  entry: string;
  hidden: Hidden;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [refusal, setRefusal] = useState<string | null>(null);
  const pane = useRef<HTMLDivElement>(null);
  // A viewer that opens takes the keyboard, so its shortcuts work at once.
  useEffect(() => pane.current?.focus({ preventScroll: true }), []);
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
      className="flex min-h-0 flex-1 flex-col bg-canvas text-on-canvas outline-none"
    >
      {view.status === "ready" ? (
        <>
          <div className="relative min-h-0 flex-1">
            <LaneGrid
              session={session}
              view={view}
              name={name}
              entry={entry}
              hidden={hidden}
              onHide={(label) => {
                setHidden(entry, label, true).then(setRefusal, (error: unknown) =>
                  setRefusal(error instanceof Error ? error.message : String(error)),
                );
              }}
            />
            {view.warnings.length > 0 && (
              <div className="absolute top-0 right-0 p-s">
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
            {refusal && (
              <div
                role="alert"
                className="absolute top-0 left-1/2 m-s flex -translate-x-1/2 items-center rounded-control bg-canvas-scrim pl-m"
              >
                {refusal}
                <IconButton
                  onCanvas
                  icon="x"
                  label="Dismiss"
                  onClick={() => setRefusal(null)}
                  className="size-row"
                />
              </div>
            )}
          </div>
          <Transport controls={session} view={view} />
        </>
      ) : (
        <Status view={view} name={name} />
      )}
    </div>
  );
}

/**
 * Plays one entry from the backend: its lanes in a grid with the transport below them, or why there is no picture
 * yet. Lanes the user hid are left out.
 */
export function VideoViewer({
  content,
  memory,
  hidden,
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
  return (
    session && (
      <Playback
        session={session}
        name={content.display_name}
        entry={content.entry.id}
        hidden={hidden}
      />
    )
  );
}
