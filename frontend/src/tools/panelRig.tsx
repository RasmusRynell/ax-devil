import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { vi } from "vite-plus/test";
import type { FileRowWire, Located } from "../generated/api";
import type { LaneFrame, Selection } from "../playback/contract";
import { FRESH_MEMORY } from "../playback/fields";
import { LANE_SETTINGS } from "../playback/fakeOpened";
import { FileScope } from "../playback/fileScope";
import { LaneSet } from "../playback/lanes";
import type { ClientMessage, LaneStatusWire } from "../playback/protocol";
import { INITIAL_VIEW, type PlaybackView } from "../playback/view";
import { fullSample } from "../present/fixtures";
import { FRESH_LIST } from "./listState";
import { ObjectsPanel } from "./ObjectsPanel";
import { EMPTY, topObject, type PageStack } from "./pageStack";

/** What the objects panel's tests share: a viewer of a lane or two, the real File scope client over a recording socket, and queries of the list. */

const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 1000,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};

export const FRAME = fullSample([
  { id: "7", class: "car", score: 0.82, confidence: 0.4, box: [0.1, 0.1, 0.3, 0.3] },
  {
    id: "12",
    class: "human",
    score: 0.9,
    confidence: 0.5,
    motion: "moving",
    box: [0.5, 0.5, 0.6, 0.7],
  },
  { id: "9", class: "car", score: 0.6, confidence: 0.9, box: [0.2, 0.5, 0.4, 0.7] },
]);

export const file = (id: string, first: number, classes = ["car"]): FileRowWire => ({
  id,
  classes,
  events: [],
  first_frame: first,
  last_frame: first + 20,
  presence: [[first, first + 20]],
});

type PanelProps = Parameters<typeof ObjectsPanel>[0];

/** The panel with the page stack and the object page's memory a viewer would keep for it. */
function Viewer({
  onNavigate,
  first = EMPTY,
  ...props
}: Omit<PanelProps, "pages" | "list" | "onList" | "memory" | "onMemory" | "onClose"> & {
  onNavigate: PanelProps["onNavigate"];
  /** The pages already open when the panel mounts, as after it was closed and opened again. */
  first?: PageStack;
}) {
  const [pages, setPages] = useState<PageStack>(first);
  const [list, onList] = useState(FRESH_LIST);
  const [memory, onMemory] = useState(FRESH_MEMORY);
  const [open, setOpen] = useState(true);
  return open ? (
    <ObjectsPanel
      {...props}
      pages={pages}
      onNavigate={(next, at) => {
        setPages(next);
        onNavigate(next, at);
      }}
      list={list}
      onList={onList}
      memory={memory}
      onMemory={onMemory}
      onClose={() => setOpen(false)}
    />
  ) : (
    <button onClick={() => setOpen(true)}>Open media tools</button>
  );
}

/** A viewer of one lane showing `FRAME`, with the real File scope client over a recording socket. */
export function mount(
  props: Partial<PanelProps> = {},
  /** A second lane showing `second`, a selection, the pages open when the panel mounts, and the frame of the first lane. */
  {
    second,
    selection = null,
    first,
    frame = FRAME,
  }: Partial<{
    second: typeof FRAME;
    selection: Selection;
    first: PageStack;
    frame: typeof FRAME;
  }> = {},
) {
  vi.useFakeTimers();
  const lanes = new LaneSet();
  lanes.open(
    [
      { label: "DPM", slot: "DPM", source: 0 },
      ...(second ? [{ label: "GT", slot: "GT", source: 1 }] : []),
    ],
    LANE_SETTINGS,
  );
  lanes.setStatus(0, READY);
  if (second) lanes.setStatus(1, READY);
  const sent: ClientMessage[] = [];
  const files = new FileScope(
    lanes,
    (message) => sent.push(message),
    (run, ms) => {
      const timer = setTimeout(run, ms);
      return () => clearTimeout(timer);
    },
  );
  let sample = frame;
  const source = {
    files,
    shown: (lane: number) =>
      ({
        columns: lane === 1 && second ? second : sample,
        classMask: { enabled: new Uint32Array(sample.maskWords), options: 0 },
        filter: null,
      }) as unknown as LaneFrame,
    frameTime: () => undefined,
    seek: vi.fn(),
  };
  const view: PlaybackView = {
    ...INITIAL_VIEW,
    status: "ready",
    frameCount: 100,
    lanes: lanes.states,
    shown: { sequenceId: 0, timestampUs: 0, periodUs: null },
    selection,
  };
  // What the viewer would select after each step, and the keys that reach the viewer's own handlers.
  const selected: (string | null)[] = [];
  const outside = vi.fn();
  const panel = (playhead: number) => (
    <div onKeyDown={outside}>
      <div />
      <Viewer
        source={source}
        first={first}
        view={{ ...view, playhead }}
        canSeek
        selectable
        width={null}
        onWidth={() => {}}
        onNavigate={(next) => selected.push(topObject(next)?.entity ?? null)}
        {...props}
      />
    </div>
  );
  const shown = render(panel(0));
  let playhead = 0;
  /** The frame on screen changes, as when the video plays. */
  const play = (next: typeof FRAME) => {
    sample = next;
    shown.rerender(panel(++playhead));
  };
  const queries = () => sent.filter((message) => message.type === "FileQuery");
  /** Lets the debounced request go and answers it. */
  const answer = (
    rows: FileRowWire[],
    over: Partial<{
      total: number;
      matched: number;
      offset: number;
      located: Located | null;
    }> = {},
  ) => {
    act(() => {
      vi.advanceTimersByTime(150);
    });
    const request = (queries().at(-1) as { request: number }).request;
    act(() =>
      files.receive({
        type: "FileRows",
        lane: 0,
        request,
        data_revision: 1,
        policy_revision: 1,
        total: rows.length,
        matched: rows.length,
        offset: 0,
        rows,
        located: null,
        ...over,
      }),
    );
  };
  return { source, files, queries, answer, selected, outside, play, ...shown };
}

export const ids = () =>
  within(screen.getByRole("listbox", { name: "Objects" }))
    .getAllByRole("option")
    .map((row) => row.firstElementChild?.textContent);
/** Types `text` into the search box and presses Return, which applies it without waiting. */
export const search = (text: string) => {
  const box = screen.getByRole("textbox", { name: "Search objects" });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
};
/** Whether a status line says `text`; a notice cut in the middle is in two parts. */
export const says = (text: string) =>
  screen.queryAllByRole("status").some((status) => status.textContent === text);
export const showFile = () => fireEvent.click(screen.getByRole("radio", { name: "File" }));
