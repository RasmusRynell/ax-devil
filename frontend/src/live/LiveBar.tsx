import type { CSSProperties } from "react";
import type { LiveFeedStatus } from "../generated/api";
import { Button } from "../kit/Button";
import { Chip, ToggleChip } from "../kit/Chips";
import type { Tone } from "../kit/look";
import {
  describe,
  headline,
  needsRetry,
  placeholderLines,
  problemLine,
  toneOf,
  utcTime,
} from "./words";
import type { LiveView } from "./view";

/** What the bar asks of the session. */
export interface LiveControls {
  pause(): void;
  resume(): void;
  retry(): void;
}

/** One feed's tag: its name and where it stands, in the tone of its state. What the state means is the tooltip. */
function FeedChip({
  name,
  status,
  behind = false,
  waiting = false,
}: {
  name: string;
  status: LiveFeedStatus;
  behind?: boolean;
  waiting?: boolean;
}) {
  // A decoder that is behind is an error whatever the connection says, until its queue drains.
  const [text, tone, title]: [string, Tone, string] =
    behind && status.status !== "Failed"
      ? ["Behind", "danger", "Behind"]
      : waiting
        ? ["Waiting for data…", "neutral", "Waiting for data…"]
        : [headline(status), toneOf(status), describe(status)];
  return (
    <Chip tone={tone} title={title}>
      {`${name}: ${text}`}
    </Chip>
  );
}

/**
 * The live viewer's bar over its picture: where the video and the overlay stand and a Retry when one is stuck or
 * failed, the UTC time and number of the frame on screen, Pause or Resume, and the media tools. With `disabled`
 * (a window that follows a presenter, whose stream the backend does not let it control) the controls only report.
 */
export function LiveBar({
  view,
  controls,
  disabled,
  tools,
  onTools,
}: {
  view: LiveView;
  controls: LiveControls;
  disabled: boolean;
  /** Whether the media tools show beside the picture. */
  tools: boolean;
  onTools: (open: boolean) => void;
}) {
  const { shown, paused, video, overlay, behind, waiting } = view;
  const time = shown ? utcTime(shown.timestampUs) : "00:00:00";
  const problems = [problemLine("Video", video), overlay && problemLine("Overlay", overlay)].filter(
    (line) => line,
  );
  const retry = (needsRetry(video) || needsRetry(overlay)) && (
    <Button
      icon="refresh-cw"
      size="small"
      className="shrink-0"
      data-cursor-id="transport:retry"
      disabled={disabled}
      onClick={() => controls.retry()}
    >
      Retry
    </Button>
  );
  return (
    <div role="toolbar" aria-label="Live" className="border-b border-line bg-panel text-fg">
      <div className="flex flex-wrap items-center gap-x-m px-m py-xs">
        {/* The feed tags give way (ending in an ellipsis, the full text on hover) before any control does. */}
        <div className="flex min-w-0 flex-1 items-center gap-m">
          <FeedChip name="Video" status={video} />
          {overlay && (
            <FeedChip name="Overlay" status={overlay} behind={behind} waiting={waiting} />
          )}
          {paused && <span className="shrink-0 text-small text-danger">PAUSED</span>}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-x-m">
          <output
            aria-label="Time"
            style={{ "--chars": "15" } as CSSProperties}
            className="chars-wide text-right text-small text-muted tabular-nums"
          >
            {time}
          </output>
          <output
            aria-label="Frame"
            style={{ "--chars": "14" } as CSSProperties}
            className="chars-wide text-right text-small text-muted tabular-nums"
          >
            {`Frame: ${shown?.sequenceId ?? 0}`}
          </output>
          <Button
            icon={paused ? "play" : "pause"}
            data-cursor-id="transport:pause"
            disabled={disabled || view.status !== "ready"}
            onClick={() => (paused ? controls.resume() : controls.pause())}
          >
            {paused ? "Resume" : "Pause"}
          </Button>
          <ToggleChip icon="layers" checked={tools} onChange={onTools}>
            Media tools
          </ToggleChip>
        </div>
      </div>
      {problems.length > 0 && (
        <div className="flex items-start gap-m px-m pb-xs">
          <div
            className="min-w-0 flex-1 text-small select-text break-words"
            title={problems.join("\n")}
          >
            {problems.map((line) => (
              <p key={line} className="m-0">
                {line}
              </p>
            ))}
          </div>
          {retry}
        </div>
      )}
    </div>
  );
}

/** What fills the pane until the first picture: the video's state and why, centred and out of the way of the pointer. */
export function NoPicture({ video }: { video: LiveFeedStatus }) {
  const [state, why] = placeholderLines(video);
  return (
    <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-m p-xl text-center text-on-canvas-muted">
      <p className="m-0">{state}</p>
      <p className="m-0">{why}</p>
    </div>
  );
}
