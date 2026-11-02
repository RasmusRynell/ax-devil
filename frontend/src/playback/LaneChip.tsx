import type { Cadence } from "./cadence";
import { reasonOf } from "../errors";
import { Icon } from "../kit/Icon";
import { Popover } from "../kit/Popover";
import { useSettings } from "../settings";
import { LaneMenu } from "./LaneMenu";
import { alignmentTone } from "./LaneMenuTiming";
import type { LaneSettings, LaneState } from "./lanes";
import type { CatalogInfoWire } from "./protocol";

/** One thing to tell the user about a lane. */
export type Notice = {
  problem: string;
  consequence?: string;
  reasons?: readonly string[];
  /** Whether more reasons exist than `reasons` lists. */
  more?: boolean;
};

/** What the lane has to report: why it failed or stopped reading, why its last change was not applied, and what its file warned of. */
export function laneNotices(lane: LaneState): Notice[] {
  const { status } = lane;
  const notices: Notice[] = [];
  if (status !== "plain" && status.status === "Failed")
    notices.push({
      problem: reasonOf(status),
      reasons: status.location === undefined ? undefined : [status.location],
    });
  if (lane.problem) notices.push({ problem: lane.problem });
  if (lane.catalogProblem)
    notices.push({
      problem: "The chosen catalog cannot be used",
      reasons: lane.catalogProblem.split("\n"),
    });
  if (status !== "plain" && status.status === "Ready") {
    if (status.ingest.state === "Incomplete")
      notices.push({
        problem: reasonOf(status.ingest),
        consequence: "The overlay ends where reading stopped.",
        reasons: status.ingest.location === undefined ? undefined : [status.ingest.location],
      });
    for (const warning of status.warnings)
      notices.push({
        problem: warning.problem,
        consequence: warning.consequence,
        reasons: warning.reasons,
        more: warning.more_reasons,
      });
  }
  return notices;
}

const TONE_ON_CANVAS = {
  danger: "text-on-canvas-danger",
  caution: "text-on-canvas-caution",
} as const;

/** The warning icon's tone, from how the lane's samples line up with the video's frames. */
function chipTone(lane: LaneState): string {
  const ready = lane.status !== "plain" && lane.status.status === "Ready" ? lane.status : null;
  const tone = alignmentTone(ready?.alignment);
  return tone ? TONE_ON_CANVAS[tone] : "";
}

/** Notices as a list: each problem with what follows from it and the reasons the backend gives. */
export function NoticeList({ notices }: { notices: readonly Notice[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-m p-0">
      {notices.map((notice, index) => (
        <li key={index} className="flex flex-col gap-xs">
          <span className="font-semibold break-words">{notice.problem}</span>
          {notice.consequence && <span className="break-words">{notice.consequence}</span>}
          {notice.reasons?.map((reason, at) => (
            <span key={at} className="text-small break-all text-muted">
              {reason}
            </span>
          ))}
          {notice.more && <span className="text-small text-muted">More are not listed</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * The name tag over a lane's top-left corner: its label, that it is loading, a button for what there is to report,
 * and the button that opens the lane's menu. A lane without a source has neither warnings nor settings.
 * With Show Lane Names off the label goes and the rest stays.
 * Without `onHide` the menu cannot hide the lane, without `onApplyAll` it cannot copy to the others.
 */
export function LaneChip({
  lane,
  catalogs,
  cadence,
  onChange,
  onHide,
  onApplyAll,
}: {
  lane: LaneState;
  catalogs: readonly CatalogInfoWire[];
  /** How evenly the video's frames are spaced, for the lane's timing. */
  cadence: Cadence | null;
  onChange: (change: Partial<LaneSettings>) => void;
  onHide?: () => void;
  onApplyAll?: () => void;
}) {
  const showName = useSettings().values["overlays.lane_names"] === true;
  const notices = laneNotices(lane);
  const loading = lane.pending || (lane.status !== "plain" && lane.status.status === "Loading");
  return (
    <div className="flex w-fit max-w-full items-center rounded-control bg-canvas-scrim text-on-canvas">
      {showName && <span className="min-w-0 truncate px-m">{lane.label}</span>}
      {loading && (
        <span role="status" className={`${showName ? "" : "pl-m "}pr-m text-on-canvas-muted`}>
          Loading…
        </span>
      )}
      {notices.length > 0 && (
        <Popover
          onCanvas
          label={`${lane.label}: warnings`}
          trigger={
            <span className={`inline-flex ${chipTone(lane)}`}>
              <Icon name="triangle-alert" />
            </span>
          }
          className="size-row justify-center"
        >
          <NoticeList notices={notices} />
        </Popover>
      )}
      {lane.source !== null && (
        <Popover
          onCanvas
          label={`${lane.label}: settings`}
          trigger={<Icon name="sliders-horizontal" />}
          className="size-row justify-center"
        >
          {(close) => (
            <LaneMenu
              lane={lane}
              catalogs={catalogs}
              cadence={cadence}
              onChange={onChange}
              onApplyAll={
                onApplyAll &&
                (() => {
                  close();
                  onApplyAll();
                })
              }
              onHide={
                onHide &&
                (() => {
                  close();
                  onHide();
                })
              }
            />
          )}
        </Popover>
      )}
    </div>
  );
}
