import { answersEvent } from "../inspect/fileEvents";
import { useMemo, useSyncExternalStore } from "react";
import type { EventLog } from "../live/eventLog";
import { frameObjects, drawnIds } from "./objects";
import type { PlaybackView } from "../playback/view";
import { itemOf, involved, type EventItem } from "./eventItem";
import { EventPage, type EventDetail } from "./EventPage";
import { whyUnavailable } from "./listStatus";
import type { ObjectSource } from "./ObjectsList";

const EMPTY: EventDetail = { objects: [], sameMoment: [], truncated: false };

/**
 * An event's page over a lane. A recording asks the backend for the event, the objects it involves and the events of
 * its moment; a live stream (`live`) has only the event's line and the frame on screen to describe it by. The page
 * reads the event identity retained by the navigation owner.
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
}: {
  source: ObjectSource;
  view: PlaybackView;
  lane: number;
  item: EventItem;
  /** The events are a live stream's. */
  live: EventLog | undefined;
  canSeek: boolean;
  /** Opens an object's page; absent where selecting objects is switched off. */
  onObject?: (id: string) => void;
  onEvent: (item: EventItem) => void;
}) {
  const events = source.files.events;
  const held = useSyncExternalStore(source.files.subscribe, () => events.event(lane));
  const unavailable = useSyncExternalStore(source.files.subscribe, () =>
    events.eventUnavailable(lane),
  );
  const timeOf = (at: number) => source.frameTime(at);
  const found = answersEvent(held, item, events.revision(lane)) ? held : null;
  const row = found?.detail?.row;
  // The file was replaced by one without this event, or with another at its place.
  const gone = found !== null && !row;
  // The frame the video shows it on follows how the lane matches, which can change while the page is open.
  const current = row && !gone ? itemOf(row, timeOf, found?.revision) : item;

  const frame = source.shown(lane);
  const shown = view.shown !== undefined;
  const present = useMemo(() => (shown ? drawnIds(frame) : null), [shown, frame]);
  const classes = useMemo(
    () => new Map(frameObjects(frame).map((object) => [object.id, object.className])),
    [frame],
  );
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
      sameMoment: found.detail.same_moment.map((other) => itemOf(other, timeOf, found.revision)),
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
      item={current}
      detail={detail ?? EMPTY}
      message={message}
      live={live !== undefined}
      frames={view.frameCount}
      current={view.shown?.sequenceId}
      timeOf={timeOf}
      present={present}
      onGo={canSeek ? () => source.seek(current.frame) : undefined}
      onObject={onObject}
      onEvent={onEvent}
    />
  );
}
