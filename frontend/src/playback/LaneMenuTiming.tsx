import { Select } from "../kit/Fields";
import type { AlignmentWire, BasisWire } from "../generated/api";
import { Switch } from "../kit/Switch";
import type { Cadence } from "./cadence";
import type { LaneSettings, LaneState } from "./lanes";
import { Group, NumberField } from "./LaneMenuFields";

/** The longest a kept sample may stay, which the backend enforces. */
const MAX_STICKY_MS = 100_000;
/** How far back a previous sample may be when the user switches from exact and the lane has not said. */
const DEFAULT_PREVIOUS_MS = 50;

type Sync = "exact" | "previous";

const ms = (us: number) => `${Math.round(us / 100) / 10} ms`;
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
  cadence: Cadence | null;
  alignment: AlignmentWire | null;
}) {
  const share = alignment && Math.floor((100 * alignment.matched) / Math.max(1, alignment.samples));
  const tone = alignmentTone(alignment);
  const toned = tone ? TONE_TEXT[tone] : "";
  return (
    <Group label="Alignment">
      {/* Long lines wrap at the width the controls give the menu instead of widening it. */}
      <div className="flex w-0 min-w-full flex-col gap-s break-words">
        <p className="m-0">
          {cadence === null
            ? "Video: unknown"
            : `Video: ${cadence.variable ? "variable" : "constant"}, ${cadence.periodsUs.map(ms).join(", ")}`}
        </p>
        <p className={`m-0 ${toned}`}>
          {alignment === null
            ? "Overlay: reading…"
            : `Overlay: ${alignment.matched} of ${alignment.samples} on a frame (${share}%)${alignment.basis === "Frame" ? "" : `, ${alignment.exact} exact`}`}
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
  onChange,
}: {
  lane: LaneState;
  /** How evenly the video's frames are spaced. */
  cadence: Cadence | null;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  const { settings } = lane;
  const ready = lane.status !== "plain" && lane.status.status === "Ready" ? lane.status : undefined;
  const bases = ready?.bases ?? [];
  const basis =
    settings.basis !== null && bases.includes(settings.basis)
      ? settings.basis
      : ready?.default_basis;
  const sync: Sync = settings.tolerance_ms === null ? "exact" : "previous";
  const { sticky } = settings;
  return (
    <>
      {ready && <Alignment cadence={cadence} alignment={ready.alignment} />}
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
              max={MAX_STICKY_MS}
              step={10}
              onCommit={(tolerance_ms) => onChange({ tolerance_ms })}
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
        {sticky.enabled && (
          <>
            <NumberField
              label="Timeout (ms)"
              value={sticky.timeout_ms}
              min={0}
              max={MAX_STICKY_MS}
              step={100}
              placeholder="Unlimited"
              onCommit={(timeout_ms) => onChange({ sticky: { ...sticky, timeout_ms } })}
            />
            <NumberField
              label="Opacity"
              value={sticky.opacity}
              min={0}
              max={1}
              step={0.1}
              onCommit={(opacity) =>
                opacity !== null && onChange({ sticky: { ...sticky, opacity } })
              }
            />
          </>
        )}
      </Group>
    </>
  );
}
