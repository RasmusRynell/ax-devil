import { Checkbox, Select } from "../kit/Fields";
import type { AlignmentWire, BasisWire } from "../generated/api";
import { Switch } from "../kit/Switch";
import type { CadenceWire } from "./protocol";
import { useSettings } from "../settings";
import {
  DEFAULT_STICKY,
  DEFAULT_SYNC,
  basisOf,
  sameSticky,
  savedTiming,
  type LaneSettings,
  type LaneState,
} from "./lanes";
import { Group, MakeDefault, NumberField } from "./LaneMenuFields";

/** The longest a sample may match back or stay kept, which the backend enforces. */
const MAX_MS = 100_000;
/** How far back a previous sample may be when the user switches from exact and the lane has not said. */
const DEFAULT_PREVIOUS_MS = 50;

type Sync = "exact" | "previous";

/** Periods shown of those the backend sends. */
const SHOWN = 3;

/** A period in milliseconds to six significant digits, as Python's `{:g}` does. */
const ms = (us: number) => `${Number((us / 1000).toPrecision(6))} ms`;

/** The percentage `matched` is of `samples`, whole, rounded half to even as Python's `{:.0f}` does; 100 for none. */
function percent(matched: number, samples: number): number {
  if (samples === 0) return 100;
  const whole = Math.floor((100 * matched) / samples);
  const twiceRest = 2 * (100 * matched - whole * samples);
  return whole + (twiceRest > samples || (twiceRest === samples && whole % 2 === 1) ? 1 : 0);
}
const seconds = (us: number) => `${(Math.abs(us) / 1e6).toFixed(1)} s`;

/** How loudly an alignment speaks: a file mostly off the frames is wrong, one that ends far from the video needs a look. */
export function alignmentTone(
  alignment: AlignmentWire | null | undefined,
): "danger" | "caution" | null {
  if (alignment?.verdict === "weak") return "danger";
  if (alignment?.verdict === "ends_far") return "caution";
  return null;
}

const TONE_TEXT = { danger: "text-danger", caution: "text-caution" } as const;

/** How evenly the video's frames are spaced and how the lane's samples land on them under its basis. */
function Alignment({
  cadence,
  alignment,
}: {
  cadence: CadenceWire | null;
  alignment: AlignmentWire | null;
}) {
  const share = alignment && percent(alignment.matched, alignment.samples);
  const tone = alignmentTone(alignment);
  const toned = tone ? TONE_TEXT[tone] : "";
  return (
    <Group label="Alignment">
      {/* Long lines wrap at the width the controls give the menu instead of widening it. */}
      <div className="flex w-0 min-w-full flex-col gap-s break-words">
        <p className="m-0">
          {cadence === null
            ? "Video: unknown"
            : `Video: ${cadence.variable ? "variable" : "constant"}, ${cadence.periods_us.slice(0, SHOWN).map(ms).join(", ")}`}
        </p>
        <p className={`m-0 ${toned}`}>
          {alignment === null
            ? "Overlay: reading…"
            : `Overlay: ${alignment.matched} of ${alignment.samples} on a frame (${share}%, ${alignment.basis === "Frame" ? `sequence ${alignment.matched}` : `exact ${alignment.exact}, tol ${alignment.tolerance_ms ?? 0} ms`})`}
        </p>
        {alignment?.ends_far && alignment.end_gap_us !== null && (
          <p className={`m-0 ${toned}`}>
            {`Ends ${seconds(alignment.end_gap_us)} ${alignment.end_gap_us < 0 ? "before" : "after"} the video`}
          </p>
        )}
      </div>
    </Group>
  );
}

const BASES: readonly (readonly [BasisWire, string])[] = [
  ["Frame", "Frame"],
  ["Time", "Time"],
];

/**
 * How the lane lines up with the video, how a sample is matched to a frame (by the basis the file offers; under time,
 * how far back one may be) and whether old samples stay on screen. A lane whose file lacks the chosen basis is
 * matched by the file's default.
 */
export function LaneMenuTiming({
  lane,
  cadence,
  live = false,
  onChange,
}: {
  lane: LaneState;
  /** How evenly the video's frames are spaced. */
  cadence: CadenceWire | null;
  /** The lane is live: there is no recording to align, so alignment is not shown. */
  live?: boolean;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  const { settings } = lane;
  const ready = lane.status !== "plain" && lane.status.status === "Ready" ? lane.status : undefined;
  const bases = ready?.bases ?? [];
  const basis = basisOf(lane);
  const sync: Sync = settings.tolerance_ms === null ? "exact" : "previous";
  const { sticky } = settings;
  const { values } = useSettings();
  const saved = savedTiming(values);
  const timeout = sticky.timeout_ms ?? settings.kept_timeout_ms;
  const limited = sticky.timeout_ms !== null;
  return (
    <>
      {ready && !live && <Alignment cadence={cadence} alignment={ready.alignment} />}
      {(bases.length > 1 || basis === "Time") && (
        <Group label="Sync">
          {bases.length > 1 && (
            <Select<BasisWire>
              label="Match by"
              value={basis ?? "Frame"}
              options={BASES.filter(([option]) => bases.includes(option))}
              onChange={(next) => onChange({ basis: next })}
            />
          )}
          {basis === "Time" && (
            <Select<Sync>
              label="Sync"
              value={sync}
              options={[
                ["exact", "Exact"],
                ["previous", "Previous"],
              ]}
              onChange={(next) =>
                onChange({ tolerance_ms: next === "exact" ? null : DEFAULT_PREVIOUS_MS })
              }
            />
          )}
          {basis === "Time" && sync === "previous" && (
            <NumberField
              label="Within (ms)"
              value={settings.tolerance_ms}
              min={0}
              max={MAX_MS}
              step={10}
              onCommit={(tolerance_ms) => onChange({ tolerance_ms })}
            />
          )}
          {basis === "Time" && (
            <MakeDefault
              setting={DEFAULT_SYNC}
              value={{ tolerance_ms: settings.tolerance_ms }}
              isDefault={settings.tolerance_ms === saved.tolerance_ms}
            />
          )}
        </Group>
      )}
      <Group label="Sticky">
        <Switch
          label="Sticky"
          checked={sticky.enabled}
          onChange={(enabled) => onChange({ sticky: { ...sticky, enabled } })}
        />
        <div className="flex items-center justify-between gap-s">
          <Checkbox
            leading
            label="Expire after (ms)"
            disabled={!sticky.enabled}
            checked={limited}
            onChange={(expires) => {
              onChange({
                sticky: { ...sticky, timeout_ms: expires ? timeout : null },
                kept_timeout_ms: timeout,
              });
            }}
          />
          <NumberField
            hideLabel
            label="Timeout (ms)"
            value={timeout}
            min={0}
            max={MAX_MS}
            step={1}
            disabled={!sticky.enabled || !limited}
            onCommit={(timeout_ms) =>
              timeout_ms !== null &&
              onChange({ sticky: { ...sticky, timeout_ms }, kept_timeout_ms: timeout_ms })
            }
          />
        </div>
        <NumberField
          label="Reused overlay opacity"
          value={sticky.opacity}
          min={0}
          max={1}
          step={0.05}
          disabled={!sticky.enabled}
          onCommit={(opacity) => opacity !== null && onChange({ sticky: { ...sticky, opacity } })}
        />
        <MakeDefault
          setting={DEFAULT_STICKY}
          value={sticky}
          isDefault={sameSticky(sticky, saved.sticky)}
        />
      </Group>
    </>
  );
}
