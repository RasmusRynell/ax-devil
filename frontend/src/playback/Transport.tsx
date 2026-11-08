import { type CSSProperties, useState } from "react";
import { TextField } from "../kit/Fields";
import { IconButton } from "../kit/IconButton";
import { Menu, type MenuEntry } from "../kit/Menu";
import { Popover } from "../kit/Popover";
import { useScrub } from "./scrub";
import { seekBand } from "./seekBand";
import type { PlaybackSession } from "./session";
import { timecode } from "./timecode";
import type { PlaybackView } from "./view";

/** What the transport asks of the session. */
export type Controls = Pick<
  PlaybackSession,
  "togglePlay" | "step" | "seek" | "setSpeed" | "frameTime"
>;

/* Dimmed to 40% while disabled, as Python draws the white icons over the scrim. */
const BUTTON = "size-row rounded-control disabled:opacity-40";

// The controls fade with the pointer's rest (see chromeVisibility) and stay while a control has the keyboard focus ring.
const CONTROLS =
  "pointer-events-auto flex flex-col rounded-t-popup border border-canvas-line bg-canvas-scrim px-l py-xs text-on-canvas shadow-float transition-opacity duration-200 ease-out-cubic motion-reduce:transition-none group-data-[chrome=hidden]:not-has-[:focus-visible]:opacity-0 group-data-[chrome=hidden]:not-has-[:focus-visible]:pointer-events-none group-data-[chrome=hidden]:not-has-[:focus-visible]:delay-200";

const PRESETS = [0.25, 0.5, 1, 1.5, 2, 4];
const PRESET_ENTRIES: readonly MenuEntry[] = PRESETS.map((speed) => ({
  kind: "check",
  id: String(speed),
  label: `${speed}×`,
}));

/** A speed of any value, for the one the presets lack; Enter sets it, and the session keeps it within its range. */
function CustomSpeed({ speed, onSet }: { speed: number; onSet: (speed: number) => void }) {
  const [text, setText] = useState(String(speed));
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const typed = Number(text);
        if (text.trim() !== "" && Number.isFinite(typed)) onSet(typed);
        else setText(String(speed));
      }}
    >
      <TextField label="Custom" value={text} onChange={setText} />
    </form>
  );
}

/** The speed button, which opens the presets with the current one checked and a field for any other speed. */
function SpeedPicker({
  speed,
  disabled,
  onChange,
}: {
  speed: number;
  disabled: boolean;
  onChange: (speed: number) => void;
}) {
  const shown = <span className="px-s tabular-nums">{speed}×</span>;
  return (
    <span data-cursor-id="transport:speed" className="flex">
      {disabled ? (
        <span className="flex h-row items-center text-on-canvas-muted">{shown}</span>
      ) : (
        <Popover onCanvas label="Speed" trigger={shown}>
          {(close) => (
            <div className="flex flex-col gap-s">
              <Menu
                entries={PRESET_ENTRIES}
                checked={new Set([String(speed)])}
                onSelect={(preset) => {
                  onChange(Number(preset.id));
                  close();
                }}
              />
              <CustomSpeed
                speed={speed}
                onSet={(typed) => {
                  onChange(typed);
                  close();
                }}
              />
            </div>
          )}
        </Popover>
      )}
    </span>
  );
}

/** The frame on screen as a field: Enter or leaving it goes to the frame typed, within the video (below 0 is 0); Escape and text that is no frame put back the frame on screen. */
function FrameField({
  shown,
  last,
  playhead,
  disabled,
  onSeek,
}: {
  shown: number | undefined;
  last: number;
  playhead: number;
  disabled: boolean;
  onSeek: (frame: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    const typed = draft?.trim();
    setDraft(null);
    if (typed === undefined || !/^-?\d+$/.test(typed)) return;
    const frame = Math.max(0, Math.min(last, Number(typed)));
    if (frame !== playhead) onSeek(frame);
  };
  return (
    <input
      type="text"
      inputMode="numeric"
      aria-label="Frame"
      data-cursor-id="transport:frame"
      autoComplete="off"
      spellCheck={false}
      disabled={disabled}
      value={draft ?? String(shown ?? "")}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        else if (event.key === "Escape") setDraft(null);
      }}
      style={{ "--chars": String(last).length } as CSSProperties}
      className="chars-wide h-row w-0 rounded-control border border-transparent bg-transparent px-xs text-right font-mono text-on-canvas tabular-nums enabled:hover:border-on-canvas-muted focus:border-on-canvas focus:outline-none disabled:text-on-canvas"
    />
  );
}

interface Props {
  controls: Controls;
  view: PlaybackView;
  /** For a seat that may not change what the window shows: the controls report the video and do nothing. */
  disabled?: boolean;
}

/**
 * The playback controls over the bottom of the video, with a frame error above them that stays when they fade: seek bar, play and step buttons, the frame and time of the
 * frame on screen, and the speed. The frame count and time describe the frame shown, or the frame picked while the seek bar is held; the seek bar the
 * position asked for. A frame that failed to decode says why.
 */
export function Transport({ controls, view, disabled = false }: Props) {
  const last = Math.max(0, view.frameCount - 1);
  const { shown, playing, frameError } = view;
  const scrub = useScrub(controls, view);
  // While the bar is held the readouts follow the frame picked, not the one on screen.
  const frame = scrub.picked ?? shown?.sequenceId;
  const timeUs = scrub.picked === null ? shown?.timestampUs : controls.frameTime(scrub.picked);
  const position = scrub.picked ?? view.playhead;
  // Any seek made here ends a pick that still waits for its own.
  const seekTo = (frame: number) => {
    scrub.clear();
    controls.seek(frame);
  };
  const stepBy = (delta: number) => {
    scrub.clear();
    controls.step(delta);
  };
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-start gap-s text-on-canvas">
      {frameError && (
        <p
          role="alert"
          className="pointer-events-auto m-0 ml-inset w-fit rounded-popup border border-canvas-line bg-canvas-scrim px-m py-xs shadow-float"
        >
          Frame {frameError.sequenceId} cannot be shown: {frameError.reason}
        </p>
      )}
      <div data-keeps-chrome="transport" className={`w-full ${CONTROLS}`}>
        <div className="relative h-l w-full">
          <div className="absolute inset-x-0 top-1/2 h-s -translate-y-1/2 overflow-hidden rounded-pill bg-canvas-hover">
            {view.cached.map(([first, final]) => {
              const { from, span } = seekBand(first, final, view.frameCount);
              return (
                <span
                  key={first}
                  style={{ "--from": from, "--span": span } as CSSProperties}
                  className="seek-band"
                />
              );
            })}
          </div>
          <input
            type="range"
            aria-label="Timeline"
            data-cursor-id="transport:seek"
            aria-valuetext={`Frame ${position} of ${last}`}
            min={0}
            max={last}
            step={1}
            disabled={disabled}
            value={position}
            onChange={(event) => scrub.change(event.target.valueAsNumber)}
            onPointerDown={(event) =>
              event.button === 0 && scrub.press(view.playhead, event.pointerId)
            }
            onBlur={scrub.release}
            style={{ "--seek-fill": last > 0 ? position / last : 0 } as CSSProperties}
            className="seek-bar relative m-0 h-l w-full"
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-xs">
          <IconButton
            onCanvas
            icon={playing ? "pause" : "play"}
            label={playing ? "Pause" : "Play"}
            data-cursor-id="transport:play"
            disabled={disabled}
            onClick={() => controls.togglePlay()}
            className={BUTTON}
          />
          <IconButton
            onCanvas
            icon="step-back"
            label="Back 1 frame"
            data-cursor-id="transport:step-back"
            disabled={disabled}
            onClick={() => stepBy(-1)}
            className={BUTTON}
          />
          <IconButton
            onCanvas
            icon="step-forward"
            label="Forward 1 frame"
            data-cursor-id="transport:step-forward"
            disabled={disabled}
            onClick={() => stepBy(1)}
            className={BUTTON}
          />
          <span className="flex-1" />
          <FrameField
            shown={frame}
            last={last}
            playhead={view.playhead}
            disabled={disabled}
            onSeek={seekTo}
          />
          <span className="font-mono text-on-canvas-muted tabular-nums">/ {last}</span>
          <output
            aria-label="Time"
            style={{ "--chars": timecode(view.durationUs).length } as CSSProperties}
            className="chars-wide border border-transparent px-xs text-right font-mono tabular-nums text-on-canvas-muted"
          >
            {timeUs === undefined ? "…" : timecode(timeUs)}
          </output>
          <SpeedPicker
            speed={view.speed}
            disabled={disabled}
            onChange={(speed) => controls.setSpeed(speed)}
          />
        </div>
      </div>
    </div>
  );
}
