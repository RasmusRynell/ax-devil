import { Select } from "../kit/Fields";
import type { BasisWire } from "../generated/api";
import { Switch } from "../kit/Switch";
import type { LaneSettings, LaneState } from "./lanes";
import { Group, NumberField } from "./LaneMenuFields";

/** The longest a kept sample may stay, which the backend enforces. */
const MAX_STICKY_MS = 100_000;
/** How far back a previous sample may be when the user switches from exact and the lane has not said. */
const DEFAULT_PREVIOUS_MS = 50;

type Sync = "exact" | "previous";

const BASES: readonly (readonly [BasisWire, string])[] = [
  ["Frame", "Frame"],
  ["Time", "Time"],
];

/**
 * How a sample is matched to a frame (by the basis the file offers; under time, how far back one may be) and whether
 * old samples stay on screen. A lane whose file lacks the chosen basis is matched by the file's default.
 */
export function LaneMenuTiming({
  lane,
  onChange,
}: {
  lane: LaneState;
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
