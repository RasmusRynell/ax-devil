import { useId, useState } from "react";
import type { CatalogInfoWire } from "../generated/api";
import { Button } from "../kit/Button";
import type { LaneSettings, LaneState } from "./lanes";
import { LaneMenuFilter } from "./LaneMenuFilter";
import { LaneMenuLook } from "./LaneMenuLook";
import { LaneMenuTiming } from "./LaneMenuTiming";

const TABS = ["Look", "Filter", "Timing"] as const;

/**
 * What the user can change about one lane, in three tabs so that it fits a small window at large text: how it looks
 * (catalog, theme, features), which objects it shows (classes, an expression) and how samples are matched to frames
 * (sync, sticky). Each change goes to `onChange` as the part of the lane's settings that differs. `onHide` is absent
 * for a seat that may not change the workspace; `onApplyAll` when the viewer has no other lane to copy to.
 */
export function LaneMenu({
  lane,
  catalogs,
  onChange,
  onHide,
  onApplyAll,
}: {
  lane: LaneState;
  /** The catalogs the backend offers for the lane to draw with. */
  catalogs: readonly CatalogInfoWire[];
  onChange: (change: Partial<LaneSettings>) => void;
  onHide?: () => void;
  /** Gives the viewer's other lanes this lane's settings, but not its visibility. */
  onApplyAll?: () => void;
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Look");
  const panel = useId();
  return (
    <div className="flex min-w-[24ch] flex-col gap-m">
      <div role="tablist" className="flex border-b border-line">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === tab}
            aria-controls={panel}
            onClick={() => setTab(name)}
            className={`-mb-hairline border-b-edge bg-transparent px-m py-s text-fg focus-ring ${name === tab ? "border-accent" : "border-transparent"}`}
          >
            {name}
          </button>
        ))}
      </div>
      <div id={panel} role="tabpanel" aria-label={tab} className="flex flex-col gap-m">
        {tab === "Look" && <LaneMenuLook lane={lane} catalogs={catalogs} onChange={onChange} />}
        {tab === "Filter" && <LaneMenuFilter lane={lane} onChange={onChange} />}
        {tab === "Timing" && <LaneMenuTiming lane={lane} onChange={onChange} />}
      </div>
      {(onApplyAll || onHide) && (
        <div className="flex flex-wrap gap-s border-t border-line pt-m">
          {onApplyAll && <Button onClick={onApplyAll}>Apply to all lanes</Button>}
          {onHide && (
            <Button icon="eye-off" onClick={onHide}>
              Hide lane
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
