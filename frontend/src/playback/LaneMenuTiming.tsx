import { Select } from "../kit/Fields";
import { Switch } from "../kit/Switch";
import type { LaneSettings, LaneState } from "./lanes";
import { Group, NumberField } from "./LaneMenuFields";

/** The longest a kept sample may stay, which the backend enforces. */
const MAX_STICKY_MS = 100_000;
/** How far back a previous sample may be when the user switches from exact and the lane has not said. */
const DEFAULT_PREVIOUS_MS = 50;

type Sync = "exact" | "previous";

/** How a sample is matched to a frame (for lanes keyed by time) and whether old samples stay on screen. */
export function LaneMenuTiming({
  lane,
  onChange,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneSettings>) => void;
}) {
  const { settings } = lane;
  const timed =
    lane.status !== "plain" && lane.status.status === "Ready" && lane.status.clock === "timestamp";
  const sync: Sync = settings.tolerance_ms === null ? "exact" : "previous";
  const { sticky } = settings;
  return (
    <>
      {timed && (
        <Group label="Sync">
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
          {sync === "previous" && (
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
