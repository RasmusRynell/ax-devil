import type { CadenceWire } from "../stream/protocol";
import { Button } from "../kit/Button";
import { Icon } from "../kit/Icon";
import { NoticeList } from "../kit/NoticeList";
import { Popover } from "../kit/Popover";
import { laneNotices } from "./laneNotices";
import { LaneMenu } from "./LaneMenu";
import { alignmentTone } from "./LaneMenuTiming";
import type { LaneSettings, LaneState } from "./lanes";
import type { CatalogInfoWire } from "../stream/protocol";

/** What the lane's chip says while the backend parses its overlay file: the lane's place among the lanes when there are several. */
export function parsingMessage(
  label: string,
  { position, count }: { position: number; count: number },
): string {
  return count > 1
    ? `Parsing overlay data (${position}/${count}): ${label}`
    : `Parsing overlay data: ${label}`;
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

/**
 * The name tag over a lane's top-left corner: its label, that it is loading, a button for what there is to report,
 * and the button that opens the lane's menu. A lane without a source has neither warnings nor settings.
 * With `hideName` (Show Lane Names off, or the info HUD covering the picture), the label goes and the rest stays.
 * Without `onHide` the menu cannot hide the lane, without `onApplyAll` it cannot copy to the others.
 */
export function LaneChip({
  lane,
  catalogs,
  cadence,
  onChange,
  onHide,
  onApplyAll,
  onReload,
  onDismissCatalogAction,
  onExport,
  hideName = false,
  live = false,
  stage,
}: {
  lane: LaneState;
  catalogs: readonly CatalogInfoWire[];
  /** How evenly the video's frames are spaced, for the lane's timing. */
  cadence: CadenceWire | null;
  onChange: (change: Partial<LaneSettings>) => void;
  onHide?: () => void;
  onApplyAll?: () => void;
  onReload?: () => void;
  /** Dismisses the action-only Apply refusal. */
  onDismissCatalogAction?: () => void;
  /** Adds Export video to the menu; see `LaneMenu`. */
  onExport?: { run: () => void; waits: boolean };
  hideName?: boolean;
  /** The lane is live: its menu shows no alignment. */
  live?: boolean;
  /** Where the lane is among the lanes shown; while the backend reads its file the chip names that stage. */
  stage?: { position: number; count: number };
}) {
  const showName = !hideName;
  const notices = laneNotices(lane);
  const loadingStatus = lane.status !== "plain" && lane.status.status === "Loading";
  // A file that is still being read has a Ready lane, but parsing goes on.
  const parsing =
    loadingStatus ||
    (lane.status !== "plain" &&
      lane.status.status === "Ready" &&
      lane.status.ingest.state === "Reading");
  // The lane's file is being parsed: the chip says which, in place of the lane's name, which the message holds.
  const named = parsing && !live && stage !== undefined;
  const status = named
    ? parsingMessage(lane.label, stage)
    : lane.pending || loadingStatus
      ? "Loading…"
      : null;
  return (
    <div className="flex w-fit max-w-full items-center rounded-control bg-canvas-scrim text-on-canvas">
      {showName && !named && <span className="min-w-0 truncate px-m">{lane.label}</span>}
      {status && (
        <span
          role="status"
          title={status}
          className={`${showName && !named ? "" : "pl-m "}min-w-0 truncate pr-m text-on-canvas-muted`}
        >
          {status}
        </span>
      )}
      {/* The buttons keep their size while a long name or message truncates. */}
      <div className="flex shrink-0">
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
            <div className="flex flex-col items-start gap-m">
              <NoticeList notices={notices} />
              {lane.catalogActionProblem && onDismissCatalogAction && (
                <Button size="small" onClick={onDismissCatalogAction}>
                  Dismiss Apply error
                </Button>
              )}
            </div>
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
                live={live}
                onChange={onChange}
                onApplyAll={
                  onApplyAll &&
                  (() => {
                    close();
                    onApplyAll();
                  })
                }
                onReload={onReload}
                onExport={
                  onExport && {
                    waits: onExport.waits,
                    run: () => {
                      close();
                      onExport.run();
                    },
                  }
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
    </div>
  );
}
