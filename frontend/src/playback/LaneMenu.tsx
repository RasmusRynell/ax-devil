import { useEffect, useId, useState, type ReactNode } from "react";
import type { CatalogInfoWire } from "../generated/api";
import { Button } from "../kit/Button";
import { Checkbox, Select } from "../kit/Fields";
import { Switch } from "../kit/Switch";
import { fetchCatalogs } from "../viewer/requests";
import type { LaneState } from "./lanes";
import type { LaneViewWire } from "./protocol";

/** The longest a kept sample may stay, which the backend enforces. */
const MAX_STICKY_MS = 100_000;
/** How far back a previous sample may be when the user switches from exact and the lane has not said. */
const DEFAULT_PREVIOUS_MS = 50;

type Sync = "exact" | "previous";

/** The catalogs the backend offers; empty until they arrive, and when they cannot be listed. */
function useCatalogs(): readonly CatalogInfoWire[] {
  const [catalogs, setCatalogs] = useState<readonly CatalogInfoWire[]>([]);
  useEffect(() => {
    const abort = new AbortController();
    fetchCatalogs(abort.signal).then(
      (info) => setCatalogs(info.catalogs),
      () => {},
    );
    return () => abort.abort();
  }, []);
  return catalogs;
}

/** A number the user edits as text; the change applies when they press Enter or leave the field. */
function NumberField({
  label,
  value,
  min,
  max,
  step,
  placeholder,
  onCommit,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step: number;
  /** What an empty field means, when it can be empty. */
  placeholder?: string;
  onCommit: (value: number | null) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    setDraft(null);
    const typed = draft.trim() === "" ? null : Number(draft);
    if (typed === null && placeholder === undefined) return;
    if (typed !== null && !Number.isFinite(typed)) return;
    const next = typed === null ? null : Math.min(max, Math.max(min, typed));
    if (next !== value) onCommit(next);
  };
  return (
    <div className="flex items-center justify-between gap-xl">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        value={draft ?? (value === null ? "" : String(value))}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => event.key === "Enter" && commit()}
        className="h-row w-[10ch] rounded-control border border-line-strong bg-popup px-s text-right text-fg tabular-nums placeholder:text-muted focus:border-accent focus:outline-none"
      />
    </div>
  );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-s border-t border-line pt-m">
      {children}
    </div>
  );
}

function Classes({
  lane,
  onChange,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneViewWire>) => void;
}) {
  if (lane.status === "plain" || lane.status.status !== "Ready" || !lane.status.filter.length)
    return null;
  const options = lane.status.filter;
  const { filter } = lane.view;
  const isOn = (id: string, fallback: boolean) => filter[id] ?? fallback;
  return (
    <Group label="Classes">
      <div className="flex items-center justify-between gap-xl">
        <span>Classes</span>
        <div className="flex gap-s">
          <Button
            disabled={options.every((option) => isOn(option.id, option.default_enabled))}
            onClick={() =>
              onChange({ filter: Object.fromEntries(options.map((option) => [option.id, true])) })
            }
          >
            Show all
          </Button>
          <Button
            disabled={Object.keys(filter).length === 0}
            onClick={() => onChange({ filter: {} })}
          >
            Reset
          </Button>
        </div>
      </div>
      {options.map((option) => (
        <Checkbox
          key={option.id}
          label={option.label}
          checked={isOn(option.id, option.default_enabled)}
          onChange={(on) => onChange({ filter: { ...filter, [option.id]: on } })}
        />
      ))}
    </Group>
  );
}

/**
 * What the user can change about one lane: its catalog, which classes show, whether old samples stay on screen, and
 * how a sample is matched to a frame (for lanes keyed by time). Each change goes to `onChange` as the part of the
 * lane's view that differs.
 */
export function LaneMenu({
  lane,
  onChange,
  onHide,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneViewWire>) => void;
  onHide: () => void;
}) {
  const catalogs = useCatalogs();
  const { view } = lane;
  const options: [string, string][] = catalogs.map((catalog) => [catalog.id, catalog.name]);
  if (!catalogs.some((catalog) => catalog.id === view.catalog))
    options.unshift([view.catalog, view.catalog]);
  const timed =
    lane.status !== "plain" && lane.status.status === "Ready" && lane.status.clock === "timestamp";
  const sync: Sync = view.tolerance_ms === null ? "exact" : "previous";
  const { sticky } = view;
  return (
    <div className="flex min-w-[24ch] flex-col gap-m">
      <Select
        label="Catalog"
        value={view.catalog}
        options={options}
        onChange={(catalog) => onChange({ catalog })}
      />
      <Classes lane={lane} onChange={onChange} />
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
              value={view.tolerance_ms}
              min={0}
              max={MAX_STICKY_MS}
              step={10}
              onCommit={(tolerance_ms) => onChange({ tolerance_ms })}
            />
          )}
        </Group>
      )}
      <div className="border-t border-line pt-m">
        <Button icon="eye-off" onClick={onHide}>
          Hide lane
        </Button>
      </div>
    </div>
  );
}
