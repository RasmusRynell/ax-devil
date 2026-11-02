import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { FileRowWire, FileScopeUnavailable } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import { LANE_DEFAULTS } from "../playback/fakeOpened";
import { FileScope } from "../playback/fileScope";
import { LaneSet } from "../playback/lanes";
import type { ClientMessage, LaneStatusWire } from "../playback/protocol";
import { INITIAL_VIEW, type PlaybackView } from "../playback/view";
import { fullSample } from "../present/fixtures";
import { ObjectsPanel } from "./ObjectsPanel";

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

const FRAME = fullSample([
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

const file = (id: string, first: number, classes = ["car"]): FileRowWire => ({
  id,
  classes,
  events: [],
  first_frame: first,
  last_frame: first + 20,
  presence: [[first, first + 20]],
});

/** A viewer of one lane showing `FRAME`, with the real File scope client over a recording socket. */
function mount(selection: PlaybackView["selection"] = null) {
  vi.useFakeTimers();
  const lanes = new LaneSet();
  lanes.open([{ label: "DPM", slot: "DPM", source: 0 }], LANE_DEFAULTS);
  lanes.setStatus(0, READY);
  const sent: ClientMessage[] = [];
  const files = new FileScope(
    lanes,
    (message) => sent.push(message),
    (run, ms) => {
      const timer = setTimeout(run, ms);
      return () => clearTimeout(timer);
    },
  );
  const source = {
    files,
    shown: () =>
      ({
        columns: FRAME,
        classMask: { enabled: new Uint32Array(FRAME.maskWords), options: 0 },
        filter: null,
      }) as unknown as LaneFrame,
    select: vi.fn(),
    seek: vi.fn(),
  };
  const view: PlaybackView = {
    ...INITIAL_VIEW,
    status: "ready",
    frameCount: 100,
    lanes: lanes.states,
    selection,
  };
  const panel = <ObjectsPanel source={source} view={view} canSeek selectable onClose={() => {}} />;
  const shown = render(panel);
  const queries = () => sent.filter((message) => message.type === "FileQuery");
  /** Lets the debounced request go and answers it. */
  const answer = (rows: FileRowWire[], over: Partial<{ total: number; matched: number }> = {}) => {
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
        ...over,
      }),
    );
  };
  return { source, files, queries, answer, ...shown };
}

const ids = () =>
  within(screen.getByRole("listbox", { name: "Objects" }))
    .getAllByRole("option")
    .map((row) => row.firstElementChild?.textContent);
const search = (text: string) =>
  fireEvent.change(screen.getByRole("textbox", { name: "Search objects" }), {
    target: { value: text },
  });
const showFile = () => fireEvent.click(screen.getByRole("radio", { name: "File" }));

beforeEach(() => vi.stubGlobal("ResizeObserver", undefined));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Frame scope", () => {
  it("lists the frame's objects, best score first, searchable and sortable, and a row selects its object", () => {
    const { source } = mount();
    expect(ids()).toEqual(["12", "7", "9"]);
    // Natural order: 9 comes before 12, as it does not as text.
    fireEvent.click(screen.getByRole("button", { name: "Sort by ID" }));
    expect(ids()).toEqual(["7", "9", "12"]);
    fireEvent.click(screen.getByRole("button", { name: /^Sort by ID/ }));
    expect(ids()).toEqual(["12", "9", "7"]);

    // A number column starts with its greatest, and each row reads its values off the frame.
    fireEvent.click(screen.getByRole("button", { name: "Sort by Conf" }));
    expect(ids()).toEqual(["9", "12", "7"]);
    expect(screen.getAllByRole("option")[1].textContent).toContain("0.5");
    expect(screen.getAllByRole("option")[1].textContent).toContain("moving");
    fireEvent.click(screen.getByRole("button", { name: "Sort by ID" }));

    search("class:car score>0.7");
    expect(ids()).toEqual(["7"]);
    expect(screen.getByText("1 of 3")).toBeTruthy();
    search("class:bus");
    expect(screen.getByText("No matching objects")).toBeTruthy();

    search("");
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Enter" });
    expect(source.select).toHaveBeenCalledWith({ lane: 0, source: 0, entity: "9" });
    expect(source.seek).not.toHaveBeenCalled();
  });
});

describe("File scope", () => {
  it("asks for the typed search and sort, lists the rows, and dims what is not in the frame", () => {
    const { source, queries, answer } = mount({ lane: 0, source: 0, entity: "7" });
    showFile();
    search("class:car");
    answer([file("7", 3), file("40", 55)], { total: 90, matched: 2 });
    expect(queries().map((q) => (q.type === "FileQuery" ? [q.query, q.sort] : []))).toEqual([
      [
        {
          groups: [[{ path: "class", op: "eq", value: "car", negated: false }]],
        },
        { key: "id", descending: false },
      ],
    ]);
    expect(ids()).toEqual(["7", "40"]);
    expect(screen.getByText("2 of 90")).toBeTruthy();
    const [here, away] = screen.getAllByRole("option");
    expect(here.getAttribute("aria-selected")).toBe("true");
    expect(here.textContent).toContain("3–23");
    expect(here.title).toBe("");
    expect(away.title).toBe("Not in this frame");

    // An object in the frame is only selected; one that is not also goes to where it first is.
    fireEvent.click(here);
    expect(source.seek).not.toHaveBeenCalled();
    fireEvent.click(away);
    expect(source.select).toHaveBeenLastCalledWith({ lane: 0, source: 0, entity: "40" });
    expect(source.seek).toHaveBeenCalledWith(55);
  });

  it("seeks to the frame under a click on the presence strip", () => {
    const { source, answer } = mount();
    showFile();
    answer([file("7", 3)]);
    const strip = screen.getByRole("option").children[2].firstElementChild as HTMLElement;
    vi.spyOn(strip, "getBoundingClientRect").mockReturnValue({ left: 10, width: 200 } as DOMRect);
    fireEvent.click(strip, { clientX: 110 });
    expect(source.seek).toHaveBeenCalledWith(50);
    expect(source.select).not.toHaveBeenCalled();
  });

  it.each<[FileScopeUnavailable, string]>([
    [{ state: "Reading" }, "Reading the file…"],
    [{ state: "Following" }, "Not available while following"],
    [{ state: "TooLarge" }, "The file is too large to list"],
    [{ state: "PlainLane" }, "This lane has no objects"],
    [{ state: "Failed", code: "Internal", params: {}, reason: "Disk full" }, "Disk full"],
  ])("says why there are no rows: %j", (unavailable, text) => {
    const { files, queries } = mount();
    showFile();
    expect(screen.getByText("Loading…")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(150);
    });
    const request = (queries().at(-1) as { request: number }).request;
    act(() => files.receive({ type: "FileScope", lane: 0, request, unavailable }));
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("stops asking for the list when Frame scope is shown again or the panel goes away", () => {
    const { files, answer, unmount } = mount();
    showFile();
    answer([file("7", 3)]);
    expect(files.view(0).page).not.toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Frame" }));
    expect(files.view(0)).toEqual({ pending: false, page: null, unavailable: null });
    showFile();
    expect(files.view(0).pending).toBe(true);
    unmount();
    expect(files.view(0)).toEqual({ pending: false, page: null, unavailable: null });
  });
});
