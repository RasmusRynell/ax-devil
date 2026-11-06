import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { act, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { InspectionView, Summary } from "./contract";
import { LANE_SETTINGS } from "./fakeOpened";
import { FRESH_MEMORY } from "./fields";
import { FileScope } from "./fileScope";
import { Inspector, type Reach } from "./Inspector";
import { LaneSet } from "./lanes";
import type { ClientMessage, FieldWire, LaneStatusWire } from "./protocol";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const SELECTION = { lane: 0, source: 0, entity: "7" };
const SUMMARY: Summary = {
  entity: "7",
  className: "car",
  score: 0.8123,
  confidence: null,
  motion: "moving",
};
const FIELDS: FieldWire[] = [
  { group: "classification", path: ["class"], value: { kind: "text", value: "car" } },
  {
    group: "debug",
    origin: "source",
    path: ["debug", "frame"],
    value: { kind: "int", value: "12" },
  },
  { group: "debug", origin: "decoder", path: ["flags", "0"], value: { kind: "bool", value: true } },
  { group: "raw", path: ["note"], value: { kind: "null" } },
];

type Shown = Exclude<InspectionView, { state: "none" }>;

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

/** The real File scope client of one Ready lane over a recording socket. */
function backend() {
  const lanes = new LaneSet();
  lanes.open(
    [
      { label: "DPM", slot: "DPM", source: 0 },
      { label: "GT", slot: "GT", source: 1 },
    ],
    LANE_SETTINGS,
  );
  lanes.setStatus(0, READY);
  lanes.setStatus(1, READY);
  const sent: ClientMessage[] = [];
  const files = new FileScope(
    lanes,
    (message) => sent.push(message),
    () => () => {},
  );
  return { files, sent };
}

/** The inspector with the memory a viewer would hold for it. */
function Viewer({ inspection, reach }: { inspection: Shown; reach: Reach }) {
  const [memory, onMemory] = useState(FRESH_MEMORY);
  return <Inspector inspection={inspection} memory={memory} onMemory={onMemory} reach={reach} />;
}

/** Frames 40 ms apart, the first at 01:02:03.004 into the day. */
const timeOf = (frame: number) => 3_723_004_000 + frame * 40_000;

function show(inspection: Shown, reach: Reach = { source: backend().files, frame: 0, timeOf }) {
  const view = render(<Viewer inspection={inspection} reach={reach} />);
  return {
    next: (other: Shown, over: Partial<Reach> = {}) =>
      view.rerender(<Viewer inspection={other} reach={{ ...reach, ...over }} />),
  };
}

const group = (name: string) => screen.getByRole("button", { name });

const ready = (
  fields: readonly FieldWire[],
  state: "loading" | "ready" | "unavailable" = "ready",
): Extract<InspectionView, { fields: readonly FieldWire[] }> => ({
  state,
  selection: SELECTION,
  summary: SUMMARY,
  fields,
  truncated: false,
});

describe("inspector", () => {
  it("shows the motion while the object is in the frame, and unknown for an object without a class", () => {
    show({ ...ready(FIELDS), summary: { ...SUMMARY, className: null } });
    expect(screen.getByText("moving")).toBeTruthy();
    expect(screen.getByText("unknown")).toBeTruthy();
  });

  it("shows the summary and, until a reply, Loading… without any group", () => {
    show(ready([], "loading"));
    expect(screen.getByText("car")).toBeTruthy();
    expect(screen.getByText("score 0.81")).toBeTruthy();
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("lists the fields in groups, the bulky ones folded, and leaves out empty groups", () => {
    show(ready(FIELDS));
    const captions = screen.getAllByRole("button", { name: /^(Classification|Debug|Raw)$/ });
    expect(captions.map((caption) => caption.getAttribute("aria-expanded"))).toEqual([
      "true",
      "false",
      "false",
    ]);
    expect(screen.queryByRole("button", { name: "Motion" })).toBeNull();
    expect(screen.queryByText("flags.0")).toBeNull();
    fireEvent.click(group("Debug"));
    expect(screen.getByText("Source")).toBeTruthy();
    expect(screen.getByText("Computed")).toBeTruthy();
    expect(screen.getByText("flags.0")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("keeps the rows mounted under the pair shown before until the next sample's arrive", () => {
    const { next } = show(ready(FIELDS));
    const caption = group("Classification");
    next(ready(FIELDS, "loading"));
    expect(screen.queryByText("Loading…")).toBeNull();
    expect(group("Classification")).toBe(caption);
    next(ready(FIELDS.slice(0, 1)));
    expect(group("Classification")).toBe(caption);
    expect(screen.queryByRole("button", { name: "Debug" })).toBeNull();
  });

  it("says why an absent object has no attributes, and not while it is in the frame", () => {
    const note = "Attributes are only available while an object is in the frame.";
    show({ state: "absent", selection: SELECTION, reason: "gone", last: SUMMARY });
    expect(screen.getByText("car")).toBeTruthy();
    expect(screen.getByText("Not in this frame")).toBeTruthy();
    expect(screen.getByText(`Not in the current frame. ${note}`)).toBeTruthy();
    expect(screen.queryByText("moving")).toBeNull();
    cleanup();
    show({ state: "absent", selection: SELECTION, reason: "filtered", last: null });
    expect(screen.getByText("Hidden by filter")).toBeTruthy();
    expect(
      screen.getByText(
        "In this frame, but hidden by the overlay filter. Attributes are only available for objects that pass it.",
      ),
    ).toBeTruthy();
    cleanup();
    show(ready(FIELDS));
    expect(screen.queryByText("Not in this frame")).toBeNull();
    expect(screen.queryByText(/Attributes are only available/)).toBeNull();
    cleanup();
    show(ready([], "unavailable"));
    expect(screen.getByText("Details are not available")).toBeTruthy();
    expect(screen.getByText("car")).toBeTruthy();
  });

  it("shows numbers rounded and copies them, and the Raw group, whole", () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show(
      ready([
        ...FIELDS,
        { group: "motion", path: ["speed"], value: { kind: "float", value: 0.123456 } },
        {
          group: "classification",
          path: ["score"],
          value: { kind: "float", value: 0.8712 },
          text: "0.87",
          fraction: 0.8712,
        },
        { group: "raw", path: ["blob"], value: { kind: "text", value: "x".repeat(300) } },
      ]),
    );
    expect(screen.getByText("0.1235")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy speed" }));
    expect(writeText).toHaveBeenLastCalledWith("0.123456");
    expect(screen.getByText("0.87")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy score" }));
    expect(writeText).toHaveBeenLastCalledWith("0.8712");
    fireEvent.click(screen.getByRole("button", { name: "Copy class" }));
    expect(writeText).toHaveBeenLastCalledWith("car");
    fireEvent.click(group("Raw"));
    fireEvent.click(screen.getByRole("button", { name: "Copy Raw" }));
    expect(writeText).toHaveBeenLastCalledWith(
      `{\n  "note": null,\n  "blob": "${"x".repeat(300)}"\n}`,
    );
  });

  it("copies the clicked row's value when several fields share a group and path", () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show(
      ready([
        {
          group: "classification",
          path: ["candidates", "car", "score"],
          value: { kind: "float", value: 0.9 },
        },
        {
          group: "classification",
          path: ["candidates", "car", "score"],
          value: { kind: "float", value: 0.4 },
        },
      ]),
    );
    const copies = screen.getAllByRole("button", { name: "Copy candidates.car.score" });
    fireEvent.click(copies[1]!);
    expect(writeText).toHaveBeenLastCalledWith("0.4");
  });

  describe("copy confirmation", () => {
    const FEEDBACK = ready([
      { group: "classification", path: ["class"], value: { kind: "text", value: "car" } },
      { group: "classification", path: ["kind"], value: { kind: "text", value: "car" } },
      { group: "motion", path: ["speed"], value: { kind: "float", value: 0.5 } },
    ]);
    const without = (path: string) => ({
      ...FEEDBACK,
      fields: FEEDBACK.fields.filter((field) => field.path[0] !== path),
    });
    const other = { ...FEEDBACK, selection: { ...SELECTION, entity: "8" } };
    const clipboard = (writeText: () => Promise<void>) =>
      vi.stubGlobal("navigator", { clipboard: { writeText } });
    const copy = (name: string) =>
      act(async () => {
        fireEvent.click(screen.getByRole("button", { name: new RegExp(`^Cop(y|ied) ${name}$`) }));
      });
    /** The rows that say they were copied. */
    const copied = () =>
      screen.queryAllByRole("button", { name: /^Copied / }).map((button) => button.ariaLabel);

    afterEach(() => vi.useRealTimers());

    it("confirms the last copied row for 1500 ms, restarting for each copy and moving to another row", async () => {
      vi.useFakeTimers();
      clipboard(async () => {});
      show(FEEDBACK);
      await copy("class");
      expect(copied()).toEqual(["Copied class"]);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      await copy("class");
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(copied()).toEqual(["Copied class"]);
      await copy("speed");
      expect(copied()).toEqual(["Copied speed"]);
      act(() => {
        vi.advanceTimersByTime(1499);
      });
      expect(copied()).toEqual(["Copied speed"]);
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(copied()).toEqual([]);
    });

    it("confirms nothing when the clipboard is missing or refuses, or the answer is out of date", async () => {
      vi.stubGlobal("navigator", {});
      const view = show(FEEDBACK);
      await copy("class");
      expect(copied()).toEqual([]);
      clipboard(async () => {
        throw new Error("denied");
      });
      await copy("class");
      expect(copied()).toEqual([]);
      const slow: (() => void)[] = [];
      const writeText = vi
        .fn<(text: string) => Promise<void>>()
        .mockImplementationOnce(() => new Promise<void>((done) => slow.push(done)))
        .mockResolvedValueOnce()
        .mockImplementationOnce(() => new Promise<void>((done) => slow.push(done)))
        .mockResolvedValueOnce()
        .mockImplementationOnce(() => new Promise<void>((done) => slow.push(done)));
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      await copy("class");
      await copy("speed");
      await act(async () => slow[0]!());
      expect(copied()).toEqual(["Copied speed"]);
      view.next(other);
      expect(copied()).toEqual([]);
      view.next(FEEDBACK);
      expect(copied()).toEqual([]);
      await copy("class");
      view.next(other);
      await act(async () => slow[1]!());
      expect(copied()).toEqual([]);
      view.next(FEEDBACK);
      await copy("speed");
      await copy("class");
      view.next(without("speed"));
      await act(async () => slow[2]!());
      expect(copied()).toEqual(["Copied class"]);
    });

    it("ends the confirmation when the copied value or field goes, and Ctrl+C copies the row holding focus", async () => {
      const writeText = vi.fn(async () => {});
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      const view = show(FEEDBACK);
      const button = screen.getByRole("button", { name: "Copy speed" });
      button.focus();
      await act(async () => {
        fireEvent.keyDown(button, { key: "c", ctrlKey: true });
      });
      expect(writeText).toHaveBeenLastCalledWith("0.5");
      expect(copied()).toEqual(["Copied speed"]);
      view.next({
        ...FEEDBACK,
        fields: [
          ...without("speed").fields,
          { group: "motion", path: ["speed"], value: { kind: "float", value: 0.75 } },
        ],
      });
      expect(copied()).toEqual([]);
      view.next(FEEDBACK);
      expect(copied()).toEqual([]);
      await copy("class");
      view.next(without("class"));
      expect(copied()).toEqual([]);
      const debug = (origin: "source" | "decoder"): FieldWire => ({
        group: "debug",
        origin,
        path: ["x"],
        value: { kind: "int", value: "7" },
      });
      view.next(ready([debug("decoder")]));
      fireEvent.click(group("Debug"));
      await copy("x");
      expect(copied()).toEqual(["Copied x"]);
      view.next(ready([debug("source"), debug("decoder")]));
      const xs = () =>
        screen.getAllByRole("button", { name: /^Cop(y|ied) x$/ }).map((button) => button.ariaLabel);
      expect(xs()).toEqual(["Copy x", "Copied x"]);
      fireEvent.click(screen.getAllByRole("button", { name: "Pin x" })[0]!);
      const pinned = within(screen.getByRole("region", { name: "Pinned" }));
      await act(async () => {
        fireEvent.click(pinned.getByRole("button", { name: /^Cop(y|ied) debug\.x$/ }));
      });
      expect(copied()).toEqual(["Copied debug.x"]);
      view.next(ready([debug("decoder")]));
      expect(copied()).toEqual([]);
    });
  });

  it("pins a field for the next object, says when it is missing, and drops the pinned list on the last unpin", () => {
    const view = show(ready(FIELDS));
    fireEvent.click(screen.getByRole("button", { name: "Pin class" }));
    const pinned = within(screen.getByRole("region", { name: "Pinned" }));
    expect(pinned.getByText("classification.class")).toBeTruthy();
    expect(pinned.getByText("car")).toBeTruthy();

    view.next(ready([{ group: "motion", path: ["speed"], value: { kind: "int", value: "2" } }]));
    const missing = within(screen.getByRole("region", { name: "Pinned" }));
    expect(missing.getByText("unavailable")).toBeTruthy();
    expect(missing.queryByRole("button", { name: /^Copy/ })).toBeNull();
    expect(screen.getByText("speed")).toBeTruthy();

    fireEvent.contextMenu(missing.getByText("classification.class"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpin field" }));
    expect(screen.queryByRole("region", { name: "Pinned" })).toBeNull();
    expect(screen.queryByRole("separator")).toBeNull();
  });

  it("offers an absent object the frames it was last and will next be seen on, where there are some", () => {
    const { files, sent } = backend();
    const seek = vi.fn();
    const absent: Shown = { state: "absent", selection: SELECTION, reason: "gone", last: SUMMARY };
    const t = show(absent, { source: files, frame: 30, timeOf, seek });
    const ask = sent.find((message) => message.type === "Appearances");
    expect(ask).toMatchObject({ lane: 0, entity: "7" });
    const answer = (spans: [number, number][], over = {}) =>
      act(() =>
        files.receive({
          type: "Appearances",
          lane: 0,
          request: (ask as { request: number }).request,
          entity: "7",
          appearances: {
            state: "Found",
            data_revision: 1,
            policy_revision: 1,
            spans,
            truncated: false,
            events: [],
            events_truncated: false,
            ...over,
          },
        }),
      );
    // An answer made under another source revision is not the object's.
    answer([[10, 20]], { data_revision: 2 });
    expect(screen.queryByText("Go to last seen")).toBeNull();

    answer([
      [10, 20],
      [40, 50],
    ]);
    fireEvent.click(screen.getByText("Go to last seen"));
    fireEvent.click(screen.getByText("Next appearance"));
    expect(seek.mock.calls).toEqual([[20], [40]]);
    expect(
      screen.getByText(/^Last seen at 01:02:03.804 \(0.4 s ago\)\. Attributes are only/),
    ).toBeTruthy();

    t.next(absent, { frame: 60 });
    expect(screen.queryByText("Next appearance")).toBeNull();
    expect(screen.getByText("Go to last seen")).toBeTruthy();
    t.next(absent, { frame: 5 });
    expect(screen.queryByText("Go to last seen")).toBeNull();
    expect(screen.getByText("Next appearance")).toBeTruthy();
    expect(
      screen.getByText(/^First seen at 01:02:03.404 \(in 0.2 s\)\. Attributes are only/),
    ).toBeTruthy();
    t.next(ready(FIELDS), { frame: 45 });
    expect(screen.queryByText("Next appearance")).toBeNull();
  });

  it("keeps what the backend holds for a lane while its page moves to another object, and frees it when the page closes or changes lane", () => {
    const { files, sent } = backend();
    const releases = () => sent.filter((message) => message.type === "FileRelease");
    const absent = (selection: Shown["selection"]): Shown => ({
      state: "absent",
      selection,
      reason: "gone",
      last: SUMMARY,
    });
    const t = show(absent(SELECTION), { source: files, frame: 0, timeOf });
    t.next(absent({ ...SELECTION, entity: "8" }));
    expect(sent.filter((message) => message.type === "Appearances")).toMatchObject([
      { lane: 0, entity: "7" },
      { lane: 0, entity: "8" },
    ]);
    expect(releases()).toEqual([]);

    t.next(absent({ lane: 1, source: 1, entity: "8" }));
    expect(releases()).toEqual([{ type: "FileRelease", lane: 0 }]);
    cleanup();
    expect(releases()).toEqual([
      { type: "FileRelease", lane: 0 },
      { type: "FileRelease", lane: 1 },
    ]);
  });

  it("says when fields were left out", () => {
    show({ ...ready(FIELDS), truncated: true });
    expect(screen.getByTitle("Some fields are not listed").textContent).toBe("Truncated");
  });

  it("draws the fields scrolled into view when the list is longer than the window", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(20);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
    vi.stubGlobal("ResizeObserver", undefined);
    const many = Array.from({ length: 300 }, (_, i) => ({
      group: "motion" as const,
      path: [`m${i}`],
      value: { kind: "int" as const, value: String(i) },
    }));
    show(ready(many));
    expect(screen.queryByText("m200")).toBeNull();

    const list = screen.getAllByRole("list").at(-1)?.parentElement as HTMLElement;
    Object.defineProperty(list, "scrollTop", { value: 200 * 20, configurable: true });
    fireEvent.scroll(list);
    expect(screen.getByText("m200")).toBeTruthy();
  });
});
