import type { PlaybackView } from "./view";

/** What the pane says while the video is not ready: indexing with its progress, reconnecting, or why it failed. */
export function Status({ view, name }: { view: PlaybackView; name: string }) {
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
