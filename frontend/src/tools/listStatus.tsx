import { reasonOf } from "../errors";
import type { FileScopeUnavailable, SearchWire } from "../generated/api";
import type { ReactNode } from "react";
import type { FileScopeView } from "../playback/fileScope";
import { SCOPES, type Scope } from "./listState";

/** What File scope says when the backend has no rows, but for a failure, which says why. */
const UNAVAILABLE: Record<Exclude<FileScopeUnavailable["state"], "Failed">, string> = {
  Reading: "Reading the file…",
  Following: "Not available while following",
  TooLarge: "The file is too large to list",
  PlainLane: "This lane has no objects",
};

/** What File scope says when the query names a field it does not search; it searches these. */
const FILE_PATHS: ReadonlySet<string> = new Set(["id", "class", "event"]);
const FILE_NOTE = "File scope filters id, class and event only";

/** What to say under the search box: File scope's note on a field it does not search, Frame scope's on a field no object has. */
export function noticeOf(
  inFile: boolean,
  wire: SearchWire,
  missing: readonly string[],
): string | null {
  if (inFile)
    return wire.groups
      .flat()
      .some(({ path }) => path !== null && !FILE_PATHS.has(path.toLowerCase()))
      ? FILE_NOTE
      : null;
  return missing.length > 0 ? `No object has ${missing.join(", ")} in this frame` : null;
}

/** What replaces the rows while the list has none to show, or `null` when it has. */
export function listMessage(
  scope: Scope,
  inFile: boolean,
  unavailable: FileScopeUnavailable | null,
  loaded: boolean,
  count: number,
  total: number,
): ReactNode {
  // A page that could not be refreshed keeps its rows; `staleOf` says so beside them.
  if (inFile && unavailable && !loaded)
    return (
      <p role="status" aria-busy={unavailable.state === "Reading"} className="m-0 p-m text-muted">
        {unavailable.state === "Failed" ? reasonOf(unavailable) : UNAVAILABLE[unavailable.state]}
      </p>
    );
  if (inFile && !loaded)
    return (
      <p role="status" aria-busy className="m-0 p-m text-muted">
        Loading…
      </p>
    );
  if (count === 0)
    return (
      <p role="status" className="m-0 pt-l text-center text-small text-muted">
        {total === 0 ? SCOPES[scope].none : "No matching objects"}
      </p>
    );
  return null;
}

/** What to say over rows that are the last ones the backend gave because it could not list them again, or `null`. */
export function staleOf({ page, unavailable }: FileScopeView): string | null {
  return page !== null && unavailable?.state === "Failed"
    ? `Out of date: ${reasonOf(unavailable)}`
    : null;
}
