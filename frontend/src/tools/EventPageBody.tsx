import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { EventLog } from "../live/eventLog";
import { frameObjects, drawnIds } from "./objects";
import type { PlaybackView } from "../playback/view";
import { itemOf, involved, type EventItem } from "./eventItem";
import { EventPage, type EventDetail } from "./EventPage";
import { whyUnavailable } from "./listStatus";
import type { ObjectSource } from "./ObjectsList";

/** How many spans of presence the backend sends per object: what a strip in a row can draw. */
const BINS = 64;

const EMPTY: EventDetail = { objects: [], sameMoment: [], truncated: false };

/**
 * An event's page over a lane. A recording asks the backend for the event, the objects it involves and the events of
 * its moment; a live stream (`live`) has only the event's line and the frame on screen to describe it by. The page
 * closes (`onGone`) when the file no longer has the event it was opened for.
 */
export function EventPageBody({
  source,
  view,
  lane,
  item,
  live,
  canSeek,
  onObject,
  onEvent,
  onGone,
}: {
  source: ObjectSource;
  view: PlaybackView;
  lane: number;
  item: EventItem;
  /** The events are a live stream's. */
  live: EventLog | undefined;
  canSeek: boolean;
  onObject: (id: string) => void;
  onEvent: (item: EventItem) => void;
  onGone: () => void;
}) {
  const events = source.files.events;
  // A new event on the same lane replaces the old one and keeps what the backend holds for the lane.
  useEffect(() => () => events.want(lane, null), [events, lane]);
  useEffect(() => {
    if (!live) events.want(lane, item.id, BINS);
  }, [events, live, lane, item.id]);
  const held = useSyncExternalStore(source.files.subscribe, () => events.event(lane));
  const unavailable = useSyncExternalStore(source.files.subscribe, () =>
    events.eventUnavailable(lane),
  );
  const found = held?.event === item.id ? held : null;
  const row = found?.detail?.row;
  // The file was replaced by one without this event, or with another at its place.
  const gone = found !== null && (!row || row.label !== item.label || row.kind !== item.kind);
  useEffect(() => {
    if (gone) onGone();
  }, [gone, onGone]);

  const frame = source.shown(lane);
  const shown = view.shown !== undefined;
  const present = useMemo(() => (shown ? drawnIds(frame) : null), [shown, frame]);
  const classes = useMemo(
    () => new Map(frameObjects(frame).map((object) => [object.id, object.className])),
    [frame],
  );
  const timeOf = (at: number) => source.frameTime(at);

  let detail: EventDetail | null = null;
  if (live)
    detail = {
      objects: involved(item).map((id) => ({
        id,
        classes: classes.has(id) ? [classes.get(id) as string] : [],
        seen: null,
        presence: [],
      })),
      sameMoment: [],
      truncated: false,
    };
  else if (found?.detail)
    detail = {
      objects: found.detail.objects,
      sameMoment: found.detail.same_moment.map((other) => itemOf(other, timeOf)),
      truncated: found.detail.same_moment_truncated,
    };
  const message =
    detail !== null || gone ? undefined : (
      <p role="status" aria-busy={unavailable === null} className="m-0 shrink-0 p-m text-muted">
        {unavailable ? whyUnavailable(unavailable, "This lane has no events") : "Loading…"}
      </p>
    );
  return (
    <EventPage
      item={item}
      detail={detail ?? EMPTY}
      message={message}
      live={live !== undefined}
      frames={view.frameCount}
      current={view.shown?.sequenceId}
      timeOf={timeOf}
      present={present}
      onGo={canSeek ? () => source.seek(item.frame) : undefined}
      onObject={onObject}
      onEvent={onEvent}
    />
  );
}
