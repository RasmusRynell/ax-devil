import type { CSSProperties } from "react";
import type { LiveFeedStatus } from "../generated/api";
import { Button } from "../kit/Button";
import { Chip, ToggleChip } from "../kit/Chips";
import { feedText, feedTone, needsRetry, utcTime } from "./words";
import type { LiveView } from "./view";

/** The side panels a live viewer opens beside the picture, one at a time. */
export type LivePanel = "objects" | "events";

/** What the bar asks of the session. */
export interface LiveControls {
  pause(): void;
  resume(): void;
  retry(): void;
}

/** One feed's tag: its name and where it stands, in the tone of its state. The full text is the tooltip. */
function FeedChip({
  name,
  status,
  behind = false,
}: {
  name: string;
  status: LiveFeedStatus;
  behind?: boolean;
}) {
  // A decoder that is behind is an error whatever the connection says, until its queue drains.
  const text = behind && status.status !== "Failed" ? "Behind" : feedText(status);
  const tone = behind && status.status !== "Failed" ? "danger" : feedTone(status);
  return (
    <Chip tone={tone} title={`${name}: ${text}`}>
      {`${name}: ${text}`}
    </Chip>
  );
}

/**
 * The live viewer's bar over its picture: where the video and the overlay stand and a Retry when one is stuck or
 * failed, the UTC time and number of the frame on screen, Pause or Resume, and the two side panels. With `disabled`
 * (a window that follows a presenter, whose stream the backend does not let it control) the controls only report.
 */
export function LiveBar({
  view,
  controls,
  disabled,
  panel,
  onPanel,
}: {
  view: LiveView;
  controls: LiveControls;
  disabled: boolean;
  panel: LivePanel | null;
  onPanel: (panel: LivePanel | null) => void;
}) {
  const { shown, paused, video, overlay, behind } = view;
  const time = shown ? `${utcTime(shown.timestampUs)} UTC` : "";
  return (
    <div
      role="toolbar"
      aria-label="Live"
      className="flex items-center gap-m border-b border-line bg-panel px-m py-xs text-fg"
    >
      {/* The feed tags give way (ending in an ellipsis, the full text on hover) before any control does. */}
      <div className="flex min-w-0 flex-1 items-center gap-m">
        <FeedChip name="Video" status={video} />
        {overlay && <FeedChip name="Overlay" status={overlay} behind={behind} />}
        {(needsRetry(video) || needsRetry(overlay)) && (
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
        )}
      </div>
      <div className="flex shrink-0 items-center gap-m">
        {shown && (
          <>
            <output
              aria-label="Time"
              style={{ "--chars": "19" } as CSSProperties}
              className="chars-wide text-right text-small text-muted tabular-nums"
            >
              {time}
            </output>
            <output
              aria-label="Frame"
              style={{ "--chars": "12" } as CSSProperties}
              className="chars-wide text-right text-small text-muted tabular-nums"
            >
              {`Frame ${shown.sequenceId}`}
            </output>
          </>
        )}
        <Button
          icon={paused ? "play" : "pause"}
          data-cursor-id="transport:pause"
          disabled={disabled || view.status !== "ready"}
          onClick={() => (paused ? controls.resume() : controls.pause())}
        >
          {paused ? "Resume" : "Pause"}
        </Button>
        <ToggleChip
          icon="layers"
          checked={panel === "objects"}
          onChange={(on) => onPanel(on ? "objects" : null)}
        >
          Objects
        </ToggleChip>
        <ToggleChip
          icon="layout-list"
          checked={panel === "events"}
          onChange={(on) => onPanel(on ? "events" : null)}
        >
          Events
        </ToggleChip>
      </div>
    </div>
  );
}
