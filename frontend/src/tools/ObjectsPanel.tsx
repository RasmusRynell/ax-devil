import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Select } from "../kit/Fields";
import { IconButton } from "../kit/IconButton";
import type { InspectorMemory } from "../playback/fields";
import { Inspector, reachOf } from "../playback/Inspector";
import type { PlaybackView } from "../playback/view";
import { back, push, step, titleOf, type PageStack } from "./pageStack";
import type { ListState } from "./listState";
import { ObjectsList, type ObjectSource, type Open } from "./ObjectsList";
import { PageHeader } from "./PageHeader";
import { PanelDivider } from "./PanelDivider";
import { VIDEO_LEAST } from "./panelWidth";

export type { ObjectSource };

/** The lane whose object a page is about, and the source the lane reads. */
export type Place = { lane: number; source: number };

/** Whether keys typed at `target` belong to the text being edited there. */
const editing = (target: EventTarget): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/**
 * The media tools of a viewer: a searchable list of the objects of one lane, in the frame on screen or in the whole
 * file. A row opens the page of its object over the list, with Back, and Previous and Next through the list as it is
 * now; one that is not in the frame on screen also goes to where it first is. `pages` is the page stack, which the
 * viewer keeps while the panel is closed, and `onNavigate` hears each change of it with the lane it is about. Esc,
 * Backspace and the mouse's Back button go back one page. A column beside the picture, which the viewer (a size
 * container) keeps `VIDEO_LEAST` wide; in a viewer too narrow for both, the column keeps the width its content needs
 * and covers the picture's right edge, as the content comes before the picture, and in one narrower than that it
 * takes the whole viewer. A divider inside its left edge resizes it; `width` is the pixels the user chose, for the
 * viewer to keep while the panel is closed, and `onWidth` hears each choice.
 */
export function ObjectsPanel({
  source,
  view,
  canSeek,
  selectable,
  frameOnly = false,
  pages,
  onNavigate,
  list,
  onList,
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
  /** Lists only the frame on screen, for a viewer with no file behind it: a live stream. */
  frameOnly?: boolean;
  pages: PageStack;
  onNavigate: (pages: PageStack, at: Place) => void;
  /** What the viewer keeps of the objects list: the page steps through the list it was opened from. */
  list: ListState;
  onList: (list: ListState) => void;
  /** What the viewer keeps of the object page: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
  /** The width the user gave the panel, or `null` for the default. */
  width: number | null;
  onWidth: (width: number) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState(width);
  const lanes = view.lanes.flatMap((lane, index) => (lane.source === null ? [] : [index]));
  // An object picked on another lane's picture switches the panel to that lane, as does opening after it.
  const picked = view.selection?.lane;
  const [chosen, setChosen] = useState<number | null>(picked ?? null);
  const [seen, setSeen] = useState(picked);
  if (picked !== seen) {
    setSeen(picked);
    if (picked !== undefined && lanes.includes(picked)) setChosen(picked);
  }
  const lane = chosen !== null && lanes.includes(chosen) ? chosen : lanes[0];
  const close = <IconButton icon="x" label="Close media tools" onClick={onClose} />;

  const aside = useRef<HTMLElement>(null);
  const page = useRef<HTMLElement>(null);
  const last = pages.entries.at(-1);
  const below = pages.entries.at(-2);
  // Keyboard focus follows a page that opens or closes only when it was in the panel, not on the video.
  const focused = useRef(false);
  const shown = `${pages.entries.length}:${last?.key.entity}`;
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

  const open: Open | null = last
    ? {
        from: last.origin ? last.key.entity : null,
        render: (steps) => (
          <section
            ref={page}
            tabIndex={-1}
            aria-label="Object page"
            className="flex min-h-0 flex-1 flex-col outline-none"
          >
            <PageHeader
              title={titleOf(last.key)}
              below={below ? titleOf(below.key) : "Objects"}
              steps={steps}
              onBack={goBack}
              onStep={(entity) => go(step(pages, { kind: "object", entity }))}
            />
            {view.inspection.state !== "none" && (
              <Inspector
                inspection={view.inspection}
                memory={memory}
                onMemory={onMemory}
                reach={reachOf(source, view, canSeek)}
              />
            )}
          </section>
        ),
      }
    : null;

  return (
    <aside
      ref={aside}
      aria-label="Media tools"
      style={
        {
          // What the list's required columns and the page need. Where that and the video's least do not both fit the
          // viewer, the column slides over the video, and where the viewer is narrower still it takes the viewer.
          "--panel-least": "min(40ch, 100cqw)",
          marginLeft: `min(0px, calc(100cqw - var(--panel-least) - ${VIDEO_LEAST}px))`,
          ...(live === null ? {} : { "--panel-width": `${live}px` }),
        } as CSSProperties
      }
      onKeyDown={onKeyDown}
      onKeyUp={(event) => event.stopPropagation()}
      onMouseDown={onMouse}
      onMouseUp={onMouse}
      onAuxClick={onMouse}
      className="relative z-10 flex w-[var(--panel-width,min(var(--spacing-panel),50%))] min-w-(--panel-least) flex-col border-l border-line bg-panel text-fg"
    >
      <PanelDivider
        docked
        onResize={(next, final) => {
          setLive(next);
          if (final) onWidth(next);
        }}
        onClose={onClose}
      />
      {lane === undefined ? (
        <>
          <div className="flex justify-end border-b border-line py-xs pr-xs">{close}</div>
          <p className="m-0 p-m text-muted">No lane has objects</p>
        </>
      ) : (
        <ObjectsList
          key={lane}
          source={source}
          view={view}
          lane={lane}
          canSeek={canSeek}
          selectable={selectable}
          frameOnly={frameOnly}
          list={list}
          onList={onList}
          mark={pages.mark?.entity ?? null}
          open={open}
          onOpen={(entity) => go(push(pages, { kind: "object", entity }))}
          trailing={
            <>
              {lanes.length > 1 && (
                <Select
                  label={<span className="sr-only">Lane</span>}
                  value={String(lane)}
                  options={lanes.map((index) => [String(index), view.lanes[index].label] as const)}
                  onChange={(value) => setChosen(Number(value))}
                />
              )}
              {close}
            </>
          }
        />
      )}
    </aside>
  );
}
