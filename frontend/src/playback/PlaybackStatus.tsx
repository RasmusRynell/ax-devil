import { reasonOf } from "../client/errors";
import { useState } from "react";
import { IconButton } from "../kit/IconButton";
import { LoadingIndicator } from "../kit/LoadingIndicator";
import type { PlaybackView } from "./view";

const RECONNECTING = "Connection lost. Reconnecting…";

/** What the live pane says while the stream is not ready: reconnecting, opening, or why it failed. */
export function Status({
  view,
  name,
  onDismiss,
}: {
  view: PlaybackView;
  name: string;
  onDismiss?: () => void;
}) {
  if (view.status === "failed" && view.failure)
    return (
      <div role="alert" className="m-auto flex max-w-full flex-col gap-s p-xl text-center">
        <p className="m-0 font-semibold break-words">{reasonOf(view.failure)}</p>
        {view.failure.location && (
          <p className="m-0 text-small break-all text-on-canvas-muted">{view.failure.location}</p>
        )}
        {onDismiss && <IconButton icon="x" label="Dismiss" onCanvas onClick={onDismiss} />}
      </div>
    );
  return (
    <div role="status" aria-busy="true" className="m-auto w-full max-w-md p-xl text-center">
      <p className="m-0">{view.status === "reconnecting" ? RECONNECTING : `Opening ${name}…`}</p>
    </div>
  );
}

/** What the offline pane shows until the video is ready: a cover that says what is being built, or why it failed. */
export function Opening({ view, name }: { view: PlaybackView; name: string }) {
  const [dismissed, setDismissed] = useState(false);
  const [failure, setFailure] = useState(view.failure);
  if (failure !== view.failure) {
    setFailure(view.failure);
    setDismissed(false);
  }
  if (view.status === "failed")
    return dismissed ? null : (
      <Status view={view} name={name} onDismiss={() => setDismissed(true)} />
    );
  return (
    <LoadingIndicator
      message={
        view.status === "indexing"
          ? `Building frame index: ${name}`
          : view.status === "reconnecting"
            ? RECONNECTING
            : ""
      }
    />
  );
}
