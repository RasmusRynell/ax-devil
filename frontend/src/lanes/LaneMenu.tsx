import { useContext, useId, useState } from "react";
import type { CatalogInfoWire } from "../generated/api";
import { Button } from "../kit/Button";
import { Checkbox } from "../kit/Fields";
import { Tabs } from "../kit/Tabs";
import { LaneViewsContext, viewName } from "../plugins/usePlugins";
import { judgedRevision, type LaneSettings, type LaneState } from "./lanes";
import { EXPORT_TIP, EXPORT_WAITS, HIDE_LANE_TIP } from "./laneContextMenu";
import { changedControls, LaneMenuFilter } from "./LaneMenuFilter";
import { LaneMenuLook } from "./LaneMenuLook";
import { LaneMenuTiming } from "./LaneMenuTiming";
import type { CadenceWire } from "../stream/protocol";
import { SECTION_HELP } from "./sectionHelp";

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
  cadence,
  live = false,
  onChange,
  onHide,
  onApplyAll,
  onReload,
  onExport,
}: {
  lane: LaneState;
  /** The catalogs the backend offers for the lane to draw with. */
  catalogs: readonly CatalogInfoWire[];
  /** How evenly the video's frames are spaced, shown with the lane's timing. */
  cadence: CadenceWire | null;
  /** A live lane has no recording to align, so its timing has no alignment. */
  live?: boolean;
  onChange: (change: Partial<LaneSettings>) => void;
  onHide?: () => void;
  /** Gives the viewer's other lanes this lane's settings, but not its visibility. */
  onApplyAll?: () => void;
  /** Asks the backend for the lane's catalog again and compiles what it answers. */
  onReload?: () => void;
  /** Exports this lane's video; `waits` while its render catalog has not loaded. Absent where nothing is exported. */
  onExport?: { run: () => void; waits: boolean };
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Look");
  const panel = useId();
  const plugins = useContext(LaneViewsContext);
  // The Filter tab counts the controls that differ from their defaults, as `Filter 2`.
  const changed = changedControls(lane);
  const names = TABS.map((name) => (name === "Filter" && changed > 0 ? `Filter ${changed}` : name));
  return (
    <div className="flex min-w-[24ch] flex-col gap-m">
      <Tabs
        label="Lane settings"
        tabs={names}
        active={{ [names[1]]: changed > 0 }}
        help={{ [names[1]]: SECTION_HELP.filter }}
        value={names[TABS.indexOf(tab)]}
        onChange={(name) => setTab(TABS[names.indexOf(name)])}
        panelId={panel}
      />
      <div id={panel} role="tabpanel" aria-label={tab} className="flex flex-col gap-m">
        {tab === "Look" && (
          <LaneMenuLook lane={lane} catalogs={catalogs} onChange={onChange} onReload={onReload} />
        )}
        {tab === "Filter" && <LaneMenuFilter lane={lane} onChange={onChange} />}
        {tab === "Timing" && (
          <LaneMenuTiming lane={lane} cadence={cadence} live={live} onChange={onChange} />
        )}
      </div>
      {plugins && (
        <div
          role="group"
          aria-label="Views"
          className="flex flex-col gap-s border-t border-line pt-m"
        >
          {plugins.views.map((plugin) => (
            <Checkbox
              key={viewName(plugin)}
              label={plugin.view.title}
              checked={plugins.shown.has(viewName(plugin))}
              onChange={() => plugins.toggle(viewName(plugin))}
            />
          ))}
        </div>
      )}
      {(onApplyAll || onHide || onExport) && (
        <div className="flex flex-wrap gap-s border-t border-line pt-m">
          {onApplyAll && (
            <Button disabled={judgedRevision(lane, catalogs) === undefined} onClick={onApplyAll}>
              Apply to all lanes
            </Button>
          )}
          {onExport && (
            <Button
              disabled={onExport.waits}
              title={onExport.waits ? EXPORT_WAITS : EXPORT_TIP}
              onClick={onExport.run}
            >
              Export video
            </Button>
          )}
          {onHide && (
            <Button icon="eye-off" title={HIDE_LANE_TIP} onClick={onHide}>
              Hide lane
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
