import { reasonOf } from "../errors";
import { noticeOf, type Notice } from "../kit/NoticeList";
import type { LaneState } from "./lanes";

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
  if (lane.catalogActionProblem)
    notices.push({
      problem: "Could not apply the catalog to all lanes",
      reasons: [lane.catalogActionProblem],
    });
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
    notices.push(...status.warnings.map(noticeOf));
  }
  return notices;
}
