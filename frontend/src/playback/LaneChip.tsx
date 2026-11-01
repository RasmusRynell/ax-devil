import { Icon } from "../kit/Icon";
import { Popover } from "../kit/Popover";
import { LaneMenu } from "./LaneMenu";
import type { LaneState } from "./lanes";
import type { LaneViewWire } from "./protocol";

/** One thing to tell the user about a lane. */
export type Notice = {
  problem: string;
  consequence?: string;
  reasons?: readonly string[];
  /** Whether more reasons exist than `reasons` lists. */
  more?: boolean;
};

/** What the lane has to report: why it failed, why its last change was not applied, and what its file warned of. */
export function laneNotices(lane: LaneState): Notice[] {
  const { status } = lane;
  const notices: Notice[] = [];
  if (status !== "plain" && status.status === "Failed")
    notices.push({ problem: status.reason, reasons: [status.location] });
  if (lane.problem) notices.push({ problem: lane.problem });
  if (status !== "plain" && status.status === "Ready")
    for (const warning of status.warnings)
      notices.push({
        problem: warning.problem,
        consequence: warning.consequence,
        reasons: warning.reasons,
        more: warning.more_reasons,
      });
  return notices;
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
 * and the button that opens the lane's menu. A lane without an overlay file has neither warnings nor settings.
 */
export function LaneChip({
  lane,
  onChange,
  onHide,
}: {
  lane: LaneState;
  onChange: (change: Partial<LaneViewWire>) => void;
  onHide: () => void;
}) {
  const notices = laneNotices(lane);
  const loading = lane.status !== "plain" && lane.status.status === "Loading";
  return (
    <div className="flex w-fit max-w-full items-center rounded-control bg-canvas-scrim text-on-canvas">
      <span className="min-w-0 truncate px-m">{lane.label}</span>
      {loading && (
        <span role="status" className="pr-m text-on-canvas-muted">
          Loading…
        </span>
      )}
      {notices.length > 0 && (
        <Popover
          onCanvas
          label={`${lane.label}: warnings`}
          trigger={<Icon name="triangle-alert" />}
          className="size-row justify-center"
        >
          <NoticeList notices={notices} />
        </Popover>
      )}
      {lane.hasOverlay && (
        <Popover
          onCanvas
          label={`${lane.label}: settings`}
          trigger={<Icon name="sliders-horizontal" />}
          className="size-row justify-center"
        >
          {(close) => (
            <LaneMenu
              lane={lane}
              onChange={onChange}
              onHide={() => {
                close();
                onHide();
              }}
            />
          )}
        </Popover>
      )}
    </div>
  );
}
