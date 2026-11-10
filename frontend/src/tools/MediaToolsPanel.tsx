import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Select } from "../kit/Fields";
import { IconButton } from "../kit/IconButton";
import { Tabs } from "../kit/Tabs";
import type { EventLog } from "../live/eventLog";
import type { InspectorMemory } from "../inspect/fields";
import { Inspector, reachOf } from "../playback/Inspector";
import { SECTION_HELP } from "../playback/sectionHelp";
import type { PlaybackView } from "../playback/view";
import { EventPageBody } from "./EventPageBody";
import { EventsList } from "./EventsList";
import { itemOf, type EventItem } from "./eventItem";
import { LiveEventsList } from "./LiveEventsList";
import type { EventsState, ListState } from "./listState";
import { ObjectsList, type ObjectSource } from "./ObjectsList";
import {
  back,
  push,
  showRoot,
  step,
  titleOf,
  type PageKey,
  type PageStack,
  type Root,
} from "./pageStack";
import { PageHeader } from "./PageHeader";
import { PanelDivider } from "./PanelDivider";
import { PANEL_CLASS, panelStyle } from "./panelWidth";
import type { Open } from "./UnderPage";

export type { ObjectSource };

/** The lane whose object or event a page is about, and the source the lane reads. */
export type Place = { lane: number; source: number };

/** What each tab is called. */
const TAB: Record<Root, string> = { objects: "Objects", events: "Events" };
const TABS = Object.values(TAB);
const ROOT_OF = Object.fromEntries(
  Object.entries(TAB).map(([root, name]) => [name, root]),
) as Record<string, Root>;

/** Whether keys typed at `target` belong to the text being edited there. */
const editing = (target: EventTarget): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/** The name a page is announced by, which changes when another page shows. */
const nameOf = (key: PageKey | undefined): string =>
  key === undefined ? "" : key.kind === "object" ? `object:${key.entity}` : `event:${key.event.id}`;

/**
 * The media tools of a viewer: a searchable list of the objects of one lane, in the frame on screen or in the whole
 * file, and one of its events, under tabs. A row opens the page of its object or event over the list, with Back, and
 * Previous and Next through the list as it is now; an event page leads to its objects and an object page to its events.
 * `pages` is the page stack, which the viewer keeps while the panel is closed, and `onNavigate` hears each change of it
 * with the lane it is about. Esc, Backspace and the mouse's Back button go back one page. A recording's events come
 * from its file; a live stream's (`log`) are the ones its viewer kept, and it has no file to list the objects of. A
 * column beside the picture, which the viewer (a size container) keeps `VIDEO_LEAST` wide; in a viewer too narrow for
 * both, the column keeps the width its content needs and the picture takes what is left (`panelStyle`). A divider
 * inside its left edge resizes it; `width` is the pixels the user chose, for the viewer to keep while the panel is
 * closed, and `onWidth` hears each choice.
 */
export function MediaToolsPanel({
  source,
  view,
  canSeek,
  selectable,
  log,
  pages,
  onNavigate,
  list,
  onList,
  events,
  onEvents,
  chosen,
  onChosen,
  memory,
  onMemory,
  width,
  onWidth,
  onClose,
}: {
  source: ObjectSource;
  view: PlaybackView;
  /** Whether this seat may move the viewer's position. */
  canSeek: boolean;
  /** Whether selecting an object is switched on. */
  selectable: boolean;
  /** The Events list of a live stream, which has no file behind it: only the frame on screen lists objects. */
  log?: EventLog;
  pages: PageStack;
  onNavigate: (pages: PageStack, at: Place) => void;
  /** What the viewer keeps of the objects list: the page steps through the list it was opened from. */
  list: ListState;
  onList: (list: ListState) => void;
  /** What the viewer keeps of the events list. */
  events: EventsState;
  onEvents: (events: EventsState) => void;
  /** The lane the lists and pages are about, which the viewer keeps with them; `null` until one is chosen. */
  chosen: number | null;
  onChosen: (lane: number) => void;
  /** What the viewer keeps of the object page: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
  /** The width the user gave the panel, or `null` for the default. */
  width: number | null;
  onWidth: (width: number) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState(width);
  const panel = useId();
  const lanes = view.lanes.flatMap((lane, index) => (lane.source === null ? [] : [index]));
  const lane = chosen !== null && lanes.includes(chosen) ? chosen : lanes[0];

  // The Events tab says how many events there are, whichever tab shows.
  const held = useSyncExternalStore(log ? log.subscribe : source.files.subscribe, () =>
    log
      ? log.getSnapshot().events
      : lane === undefined
        ? undefined
        : source.files.events.view(lane).page?.total,
  );
  // The count is asked for when the Events list, which asks for the events itself, is not showing.
  const counted = held !== undefined;
  useEffect(() => {
    if (!log && lane !== undefined && pages.root === "objects" && !counted)
      source.files.events.request(lane, "", 0, 1, null, null);
  }, [log, lane, source, pages.root, counted]);
  // What the backend kept of the lane's events goes with the panel, or with the lane.
  useEffect(
    () => () => {
      if (lane !== undefined) source.files.events.release(lane);
    },
    [source, lane],
  );

  const aside = useRef<HTMLElement>(null);
  const page = useRef<HTMLElement>(null);
  const last = pages.entries.at(-1);
  const below = pages.entries.at(-2);
  // Keyboard focus follows a page that opens or closes only when it was in the panel, not on the video.
  const focused = useRef(false);
  const shown = `${pages.entries.length}:${nameOf(last?.key)}`;
  const was = useRef(shown);
  useLayoutEffect(() => {
    const changed = was.current !== shown;
    const inside = focused.current;
    was.current = shown;
    focused.current = false;
    // Whoever is working the header's buttons keeps them.
    if (!changed || !inside || document.activeElement?.closest("header")) return;
    const list = aside.current?.querySelector<HTMLElement>('[role="listbox"]');
    (page.current ?? list ?? aside.current)?.focus({ preventScroll: true });
  });
  const go = (next: PageStack) => {
    const laneSource = lane === undefined ? null : view.lanes[lane].source;
    if (lane === undefined || laneSource === null) return;
    focused.current = aside.current?.contains(document.activeElement) ?? false;
    onNavigate(next, { lane, source: laneSource });
  };
  const goBack = () => go(back(pages));
  const swallow = useRef(false);

  const onKeyDown = (event: KeyboardEvent) => {
    const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
    const own = plain && !event.shiftKey && (event.key === "Escape" || event.key === "Backspace");
    // The viewer's own Escape does not run: it is spent going back.
    if (own && last && !event.nativeEvent.defaultPrevented && !editing(event.target)) {
      event.preventDefault();
      event.stopPropagation();
      goBack();
    } else if (plain && event.key !== "Escape") event.stopPropagation();
  };
  // The mouse's Back button goes back on press; its release is not the browser's to turn into history.
  const onMouse = (event: MouseEvent) => {
    if (event.button !== 3) return;
    if (event.type === "mousedown") swallow.current = last !== undefined;
    if (!swallow.current) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.type === "mousedown") goBack();
  };

  const openObject = (entity: string) => go(push(pages, { kind: "object", entity }));
  const openEvent = (event: EventItem) => go(push(pages, { kind: "event", event }));
  const titleBelow = below ? titleOf(below.key) : TAB[pages.root];
  // The list the page was opened from steps through its rows; one pushed from another page steps nowhere.
  const from = (key: PageKey | undefined, root: Root): string | null =>
    last && last.origin === root && key?.kind === (root === "objects" ? "object" : "event")
      ? key.kind === "object"
        ? key.entity
        : String(key.event.id)
      : null;
  const open = (root: Root): Open | null =>
    last
      ? {
          from: from(last.key, root),
          render: (steps) => (
            <section
              ref={page}
              tabIndex={-1}
              aria-label={last.key.kind === "object" ? "Object page" : "Event page"}
              className="flex min-h-0 flex-1 flex-col outline-none"
            >
              <PageHeader
                title={titleOf(last.key)}
                below={titleBelow}
                steps={steps}
                onBack={goBack}
                onStep={(key) => go(step(pages, key))}
              />
              {last.key.kind === "event" ? (
                <EventPageBody
                  source={source}
                  view={view}
                  lane={lane as number}
                  item={last.key.event}
                  live={log}
                  canSeek={canSeek}
                  onObject={selectable ? openObject : undefined}
                  onEvent={openEvent}
                  onGone={goBack}
                />
              ) : (
                view.inspection.state !== "none" && (
                  <Inspector
                    inspection={view.inspection}
                    memory={memory}
                    onMemory={onMemory}
                    reach={reachOf(source, view, canSeek)}
                    onEvent={(row) => openEvent(itemOf(row, (at) => source.frameTime(at)))}
                  />
                )
              )}
            </section>
          ),
        }
      : null;

  const header = (
    <Tabs
      label="Media tools"
      tabs={TABS}
      help={SECTION_HELP}
      value={TAB[pages.root]}
      onChange={(name) => go(showRoot(pages, ROOT_OF[name]))}
      panelId={panel}
      counts={{ [TAB.events]: held }}
      trailing={
        <>
          {lanes.length > 1 && lane !== undefined && (
            <Select
              label={<span className="sr-only">Lane</span>}
              value={String(lane)}
              options={lanes.map((index) => [String(index), view.lanes[index].label] as const)}
              onChange={(value) => onChosen(Number(value))}
            />
          )}
          <IconButton icon="x" label="Close media tools" onClick={onClose} />
        </>
      }
    />
  );
  const marked = pages.mark?.kind === "event" ? pages.mark.event.id : null;

  return (
    <aside
      ref={aside}
      id={panel}
      aria-label="Media tools"
      style={panelStyle(live)}
      onKeyDown={onKeyDown}
      onKeyUp={(event) => event.stopPropagation()}
      onMouseDown={onMouse}
      onMouseUp={onMouse}
      onAuxClick={onMouse}
      className={PANEL_CLASS}
    >
      <PanelDivider
        label="Resize media tools"
        onResize={(next, final) => {
          setLive(next);
          if (final) onWidth(next);
        }}
        onClose={onClose}
      />
      {lane === undefined ? (
        <>
          <div className="flex justify-end border-b border-line py-xs pr-xs">
            <IconButton icon="x" label="Close media tools" onClick={onClose} />
          </div>
          <p className="m-0 p-m text-muted">No lane has objects</p>
        </>
      ) : pages.root === "events" ? (
        log ? (
          <LiveEventsList
            log={log}
            view={view}
            state={events}
            onState={onEvents}
            mark={marked}
            open={open("events")}
            onOpen={openEvent}
            header={header}
          />
        ) : (
          <EventsList
            key={lane}
            source={source}
            view={view}
            lane={lane}
            state={events}
            onState={onEvents}
            mark={marked}
            open={open("events")}
            onOpen={openEvent}
            header={header}
          />
        )
      ) : (
        <ObjectsList
          key={lane}
          source={source}
          view={view}
          lane={lane}
          canSeek={canSeek}
          selectable={selectable}
          frameOnly={log !== undefined}
          list={list}
          onList={onList}
          mark={pages.mark?.kind === "object" ? pages.mark.entity : null}
          open={open("objects")}
          onOpen={openObject}
          header={header}
        />
      )}
    </aside>
  );
}
