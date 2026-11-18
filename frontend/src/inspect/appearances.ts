/**
 * Where one object appears on a lane's video, as the backend answers it for the object a lane's page is about. Asked
 * when the page opens and again when the answer would change, by `FileRequests`; the lane's revisions, owner and socket
 * are shared with the objects list.
 */

import type { EventRowWire } from "../generated/api";
import {
  basisOf,
  FileRequests,
  type Basis,
  type FileRequestSpec,
  type FileRequestsHost,
} from "./fileRequests";

/** The frames an object is shown on, as inclusive `[first, last]` spans, ascending, with a frame between two. */
export type Spans = readonly (readonly [number, number])[];

/** Where the object appears; replaced, never mutated, when it changes. */
export type Appearance = {
  /** History revision that placed these events. */
  revision: number;
  entity: string;
  spans: Spans;
  /** More spans exist than were sent: these are the first ones. */
  truncated: boolean;
  /** The events that involve the object, in frame order. */
  events: readonly EventRowWire[];
  /** More events involve the object than were sent: these are the first ones. */
  eventsTruncated: boolean;
};

/** What is wanted is the object's id; an answer about another object is dropped. */
const APPEARANCES: FileRequestSpec<"Appearances", string, Basis, Appearance> = {
  kind: "Appearances",
  shape: "lookup",
  basis: basisOf,
  message: (lane, request, entity) => ({ type: "Appearances", lane, request, entity }),
  page: (body, entity, revision) =>
    body.entity === entity
      ? {
          entity,
          revision,
          spans: body.spans,
          truncated: body.truncated,
          events: body.events,
          eventsTruncated: body.events_truncated,
        }
      : null,
};

export class Appearances {
  /** The asking, for the File scope to drive. */
  readonly requests: FileRequests<"Appearances", string, Basis, Appearance>;

  constructor(host: FileRequestsHost) {
    this.requests = new FileRequests(APPEARANCES, host);
  }

  /** Where the object the lane wants appears; `null` until known, and while the backend cannot say. */
  view(lane: number): Appearance | null {
    return this.requests.view(lane).page;
  }

  /** Wants to know where `entity` appears on the lane, or nothing with `null`, as when its page closes. */
  want(lane: number, entity: string | null): void {
    if ((this.requests.wanted(lane) ?? null) === entity) return;
    if (entity === null) this.requests.release(lane);
    else this.requests.want(lane, entity);
  }
}
