import { useState } from "react";
import { IconButton } from "../kit/IconButton";
import { MAX_SPEED } from "./clock";
import type { PlaybackSession, PlaybackView } from "./session";

/** What the transport asks of the session. */
export type Controls = Pick<PlaybackSession, "togglePlay" | "step" | "seek" | "setSpeed">;

/** Microseconds as `m:ss.cc`, or `h:mm:ss.cc` from one hour. */
function timecode(us: number): string {
  const total = Math.max(0, Math.round(us / 10_000));
  const minutes = Math.floor(total / 6000) % 60;
  const hours = Math.floor(total / 360_000);
  const seconds = `${String(Math.floor(total / 100) % 60).padStart(2, "0")}.${String(total % 100).padStart(2, "0")}`;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`
    : `${minutes}:${seconds}`;
}

const BUTTON = "size-row rounded-control";

/** The speed field: edits commit as they become a number; leaving it shows the speed the session settled on. */
function SpeedField({ speed, onChange }: { speed: number; onChange: (speed: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label className="flex items-center">
      <span className="sr-only">Speed</span>
      <input
        type="number"
        max={MAX_SPEED}
        step={0.1}
        value={draft ?? String(speed)}
        onChange={(event) => {
          setDraft(event.target.value);
          if (event.target.value !== "" && Number.isFinite(event.target.valueAsNumber))
            onChange(event.target.valueAsNumber);
        }}
        onBlur={() => setDraft(null)}
        className="h-row w-[6ch] rounded-control border border-transparent bg-transparent px-xs text-right text-on-canvas tabular-nums hover:border-on-canvas-muted focus:border-on-canvas focus:outline-none"
      />
      <span aria-hidden>×</span>
    </label>
  );
}

interface Props {
  controls: Controls;
  view: PlaybackView;
  /** Whether the controls show; hidden controls stay in the page for the keyboard. */
  visible: boolean;
}

/**
 * The playback controls over the bottom of the video: seek bar, play and step buttons, the frame and time of the
 * frame on screen, and the speed. The frame count and time describe the frame shown, the seek bar the position asked
 * for. A frame that failed to decode says why.
 */
export function Transport({ controls, view, visible }: Props) {
  const last = view.frameCount - 1;
  const { shown, playing, frameError } = view;
  return (
    <div
      className={`absolute inset-x-0 bottom-0 flex flex-col gap-xs bg-linear-to-t from-canvas-scrim to-transparent px-m pt-xl pb-s text-on-canvas transition-opacity motion-reduce:transition-none ${visible ? "" : "pointer-events-none opacity-0"}`}
    >
      {frameError && (
        <p role="alert" className="m-0 w-fit rounded-control bg-canvas-scrim px-m py-xs">
          Frame {frameError.sequenceId} cannot be shown: {frameError.reason}
        </p>
      )}
      <input
        type="range"
        aria-label="Timeline"
        aria-valuetext={`Frame ${view.playhead} of ${last}`}
        min={0}
        max={last}
        step={1}
        value={view.playhead}
        onChange={(event) => controls.seek(event.target.valueAsNumber)}
        className="m-0 h-icon w-full accent-on-canvas"
      />
      <div className="flex flex-wrap items-center gap-x-xs">
        <IconButton
          onCanvas
          icon={playing ? "pause" : "play"}
          label={playing ? "Pause" : "Play"}
          onClick={() => controls.togglePlay()}
          className={BUTTON}
        />
        <IconButton
          onCanvas
          icon="step-back"
          label="Back 1 frame"
          onClick={() => controls.step(-1)}
          className={BUTTON}
        />
        <IconButton
          onCanvas
          icon="step-forward"
          label="Forward 1 frame"
          onClick={() => controls.step(1)}
          className={BUTTON}
        />
        <span className="flex-1" />
        <output aria-label="Frame" className="px-xs tabular-nums">
          {shown?.sequenceId ?? "…"} / {last}
        </output>
        <output aria-label="Time" className="px-xs tabular-nums">
          {shown ? timecode(shown.timestampUs) : "…"}
        </output>
        <SpeedField speed={view.speed} onChange={(speed) => controls.setSpeed(speed)} />
      </div>
    </div>
  );
}
