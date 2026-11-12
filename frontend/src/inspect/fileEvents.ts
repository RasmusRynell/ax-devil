/**
 * The events a lane's whole overlay file holds, as the client asks for them: a page of the lane's Events list, and one
 * event with its objects. Both are asked and kept by `FileRequests`, and apart, so a page that opens over the list does
 * not disturb it. The list is also asked again, within `FRAME_MS`, when the frame it was asked about is not the one on
 * screen.
 */

import type { EventDetailWire, EventRowWire, FileScopeUnavailable } from "../generated/api";
import { parseSearch } from "../search/query";
import {
  basisOf,
  FileRequests,
  type Basis,
  type FileRequestSpec,
  type FileRequestsHost,
  type FileView,
} from "./fileRequests";

/** The most often a list is asked again only because the frame on screen moved. */
const FRAME_MS = 250;

/** One page of a lane's Events list. */
export type EventsPage = {
  /** The data revision it was listed under. */
  revision: number;
  /** How many events the video shows. */
  total: number;
  /** How many of them the search matches, which is how many rows the list has. */
  matched: number;
  /** The position in the list of the first of `rows`. */
  offset: number;
  rows: readonly EventRowWire[];
  /** Where the event asked about is in the whole list, whichever page this is; `null` when none was or it is not there. */
  located: { event: number; row: number } | null;
  /** The frame the page was asked about, if any. */
  frame: number | null;
  /** How many rows are on frames at or before `frame`: where the line for it goes. */
  frameRow: number | null;
};

/** What a lane's Events list shows now. */
export type EventsView = FileView<EventsPage>;

/** The navigation identity a detail request was made for, independent of reused file indices. */
export type EventIdentity = {
  id: number;
  anchor?: EventRowWire["anchor"];
  frame: number;
  revision?: number;
};

/** Whether two references name the same event at the same place in the same history revision. */
export function sameEventIdentity(left: EventIdentity, right: EventIdentity): boolean {
  return (
    left.id === right.id &&
    left.frame === right.frame &&
    left.revision === right.revision &&
    (left.anchor === right.anchor ||
      (left.anchor !== undefined &&
        right.anchor !== undefined &&
        left.anchor.every((byte, index) => byte === right.anchor?.[index])))
  );
}

/** An answer belongs only to the navigation identity it was requested for, in the expected current revision. */
export function answersEvent(
  view: EventView | null,
  identity: EventIdentity,
  revision: number,
): view is EventView {
  return (
    view !== null &&
    view.identity !== null &&
    view.revision === revision &&
    sameEventIdentity(view.identity, identity)
  );
}

/** One event of a lane, as the backend answered it; `detail` is `null` when the file has no such event. */
export type EventView = {
  /** Original anchor, frame and revision sent with the request, including on a FindEvent answer. */
  identity: EventIdentity | null;
  event: number;
  /** The data revision it was read under. */
  revision: number;
  detail: EventDetailWire | null;
};

/** What a lane's list is wanted to be. */
type Wanted = {
  text: string;
  offset: number;
  limit: number;
  locate: number | null;
  frame: number | null;
};

const EVENTS: FileRequestSpec<"Events", Wanted, Basis, EventsPage> = {
  kind: "Events",
  shape: "list",
  basis: basisOf,
  message: (lane, request, { text, offset, limit, locate, frame }) => ({
    type: "EventQuery",
    lane,
    request,
    query: parseSearch(text).wire,
    offset,
    limit,
    locate,
    frame,
  }),
  page: (body, { locate, frame }, revision) => ({
    revision,
    total: body.total,
    matched: body.matched,
    offset: body.offset,
    rows: body.rows,
    located: body.located !== null && locate !== null ? { event: locate, row: body.located } : null,
    frame,
    frameRow: body.frame_row,
  }),
  // The frame moved on while the question was out.
  again: (_, asked, wanted) =>
    wanted.frame !== null && wanted.frame !== asked.frame ? { ms: FRAME_MS, pending: false } : null,
};

/** What an event is wanted to be: which, and how finely its objects' presence is binned. */
type Event = {
  event: number;
  bins: number;
  previous?: { anchor: EventRowWire["anchor"]; frame: number; revision: number };
};

const EVENT: FileRequestSpec<"Event", Event, Basis, EventView> = {
  kind: "Event",
  shape: "lookup",
  basis: basisOf,
  message: (lane, request, { event, bins, previous }, basis) =>
    previous && previous.revision !== basis.data
      ? {
          type: "FindEvent",
          lane,
          request,
          event,
          anchor: previous.anchor,
          frame: previous.frame,
          bins,
        }
      : { type: "Event", lane, request, event, bins },
  page: (body, wanted, revision) =>
    body.event === wanted.event
      ? {
          event: wanted.event,
          revision,
          detail: body.detail,
          identity: wanted.previous ? { id: wanted.event, ...wanted.previous } : null,
        }
      : null,
};

export class FileEvents {
  /** The asking of the Events list, for the File scope to drive. */
  readonly list: FileRequests<"Events", Wanted, Basis, EventsPage>;
  /** The asking of one event, for the File scope to drive. */
  readonly one: FileRequests<"Event", Event, Basis, EventView>;

  constructor(private readonly host: FileRequestsHost) {
    this.list = new FileRequests(EVENTS, host);
    this.one = new FileRequests(EVENT, {
      ...host,
      released: (lane) => {
        host.send({ type: "EventRelease", lane });
        host.released(lane);
      },
    });
  }

  /** What the lane's Events list shows now; the same object until it changes. */
  view(lane: number): EventsView {
    return this.list.view(lane);
  }

  /** The current history revision, used by the navigation owner. */
  revision(lane: number): number {
    return basisOf(this.host.lanes, lane, this.host.owner()).data;
  }

  /** The event the lane wants, once the backend has answered; `null` until then. */
  event(lane: number): EventView | null {
    return this.one.view(lane).page;
  }

  /** Why the lane's event is not known, once the backend has said so; `null` otherwise. */
  eventUnavailable(lane: number): FileScopeUnavailable | null {
    return this.one.view(lane).unavailable;
  }

  /** What the lane's event list membership depends on (matching basis, data and policy revision); a list asked under another differs. */
  basis(lane: number): string {
    return this.list.basis(lane);
  }

  /**
   * Wants the lane's list to be `limit` rows from `offset` of the events `text` searches. `locate` names an event whose
   * row in the whole list the page tells too; `frame` a frame whose place in it does. Asking for what is already wanted
   * does nothing; a change of `frame` alone is sent within `FRAME_MS`, however often it changes, the others once they
   * have paused for `DEBOUNCE_MS`.
   */
  request(
    lane: number,
    text: string,
    offset: number,
    limit: number,
    locate: number | null,
    frame: number | null,
  ): void {
    const wanted: Wanted = { text, offset, limit, locate, frame };
    const held = this.list.wanted(lane);
    if (held) {
      const { frame: before, ...rest } = held;
      const { frame: now, ...same } = wanted;
      if (JSON.stringify(rest) === JSON.stringify(same)) {
        // A frame the page was not asked about is sent after a pause; one already pending is not pushed back, and none
        // is sent while an answer is awaited, which a newer request would make stale: the answer sends it.
        this.list.amend(lane, wanted, now !== before && now !== null ? FRAME_MS : null);
        return;
      }
    }
    this.list.want(lane, wanted);
  }

  /** Asks for the wanted list now, instead of when the pause for more changes ends. */
  flush(lane: number): void {
    this.list.flush(lane);
  }

  /** Stops wanting the lane's list, as when the panel is gone. */
  release(lane: number): void {
    this.list.release(lane);
  }

  /** Wants `event` of the lane, with presence in at most `bins` spans per object, or nothing with `null`. */
  want(lane: number, event: number | null, bins = 0, previous?: Event["previous"]): void {
    const held = this.one.wanted(lane);
    if (event === null) {
      this.one.release(lane);
    } else if (
      held?.event !== event ||
      held.bins !== bins ||
      JSON.stringify(held.previous) !== JSON.stringify(previous)
    )
      this.one.want(lane, {
        event,
        bins,
        previous,
      });
  }
}
