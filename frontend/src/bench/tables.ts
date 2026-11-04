/**
 * The objects list in a real page: the real `ObjectsPanel` over a made-up viewer, driven the way a user and a playing
 * video drive it. Times how long the first rows take to paint, the frame intervals while scrolling and while a column
 * divider is dragged, and what a new video frame every animation frame costs with the panel open and closed. Run by
 * `BENCH_SUITE=tables make bench-browser`; only a run on the real display counts (`desktop/bench.cjs`).
 */

import { createElement, type ReactElement } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import type { LaneFrame } from "../playback/contract";
import type { FieldGroupWire, FieldWire } from "../playback/protocol";
import { FRESH_MEMORY, type InspectorMemory } from "../playback/fields";
import { Inspector } from "../playback/Inspector";
import { LANE_SETTINGS } from "../playback/fakeOpened";
import { LaneSet } from "../playback/lanes";
import { INITIAL_VIEW, type PlaybackView } from "../playback/view";
import { FRESH_LIST, type ListState } from "../tools/listState";
import { ObjectsPanel } from "../tools/ObjectsPanel";
import { EMPTY } from "../tools/pageStack";
import { rememberedWidths } from "../tools/table/fit";
import "../style.css";
import { summarize } from "./stats";
import { FakeFiles, READY, RING, ringOf } from "./tablesData";
import type { Settings, Stat } from "./types";

/** The refresh interval the budget is written for: scrolling and resizing keep to it at 100000 rows. */
const BUDGET = 16.7;
/** An interval this much over a refresh is a frame that was late. */
const LATE = BUDGET * 1.1;
/** Fields in the inspector scenarios; the backend caps an object at 4096. */
const FIELDS = 4000;

export type TableName =
  | "first rows"
  | "scroll"
  | "resize"
  | "playback open"
  | "playback closed"
  | "inspector first rows"
  | "inspector scroll"
  | "inspector pin";
export type TableScope = "Frame" | "File" | "Fields";

/** One thing to time: `rows` objects in the frame (Frame scope) or matches in the file (File scope), for `seconds`. */
export interface TableScenario {
  name: TableName;
  scope: TableScope;
  rows: number;
  seconds: number;
}

/** What one scenario measured, in milliseconds: the work it asks of the page and the animation frame intervals. */
export interface TableResult {
  scenario: TableScenario;
  /** What the work is: time to paint, scroll event to updated rows, pointer move with layout, or a render. */
  workOf: string;
  work: Stat;
  /** Intervals between animation frames; empty for the scenarios that are not driven by frames. */
  intervals: Stat;
  late: number;
}

export interface TablesApi {
  plan(settings: Settings): TableScenario[];
  run(scenario: TableScenario): Promise<TableResult>;
  report(results: TableResult[]): string;
}

/** The scenarios to run: every one with 5000 objects in the frame and 100000 in the file; `smoke` is small and short. */
export function plan(settings: Settings): TableScenario[] {
  const smoke = Boolean(settings.smoke);
  const objects = Number(settings.counts || (smoke ? 100 : 5000));
  const file = smoke ? 1000 : 100_000;
  const seconds = Number(settings.seconds || (smoke ? 1 : 2));
  const scenario = (name: TableName, scope: TableScope, secs = seconds): TableScenario => ({
    name,
    scope,
    rows: scope === "Frame" ? objects : scope === "File" ? file : smoke ? 200 : FIELDS,
    seconds: secs,
  });
  return [
    scenario("first rows", "Frame"),
    scenario("first rows", "File"),
    scenario("scroll", "Frame"),
    scenario("scroll", "File"),
    scenario("resize", "Frame", Math.min(1, seconds)),
    scenario("resize", "File", Math.min(1, seconds)),
    scenario("playback closed", "Frame"),
    scenario("playback open", "Frame"),
    scenario("inspector first rows", "Fields"),
    scenario("inspector scroll", "Fields"),
    scenario("inspector pin", "Fields"),
  ];
}

/** A viewer 1280 x 720 with the panel in a column beside the picture, as it is in a wide window. */
class Rig {
  readonly host = document.createElement("div");
  readonly root: Root;
  readonly #ring: LaneFrame[];
  readonly #view: PlaybackView;
  readonly #source;
  #current = 0;
  #open = false;
  #list: ListState = FRESH_LIST;
  playhead = 0;

  constructor(readonly scenario: TableScenario) {
    this.host.style.cssText =
      "position:relative;display:flex;width:1280px;height:720px;container-type:size";
    document.body.append(this.host);
    this.root = createRoot(this.host);
    const lanes = new LaneSet();
    lanes.open([{ label: "bench", slot: "bench", source: 0 }], LANE_SETTINGS);
    lanes.setStatus(0, READY);
    this.#view = { ...INITIAL_VIEW, status: "ready", frameCount: 1000, lanes: lanes.states };
    const objects = scenario.scope === "Frame" ? scenario.rows : 5000;
    this.#ring = ringOf(objects);
    this.#source = {
      files: new FakeFiles(scenario.scope === "File" ? scenario.rows : 0),
      shown: () => this.#ring[this.#current],
      frameTime: () => undefined,
      seek: () => {},
    };
  }

  /** The viewer with the panel open or without it, showing the sample of `frame`; renders before returning. */
  show(open: boolean, frame = 0): void {
    this.#current = frame % RING;
    this.#open = open;
    flushSync(() => this.#render());
  }

  /** The list state lives here, as in the viewer: choosing a scope or a sort re-renders the panel with it. */
  #render(): void {
    const view = { ...this.#view, playhead: ++this.playhead };
    const panel = createElement(ObjectsPanel, {
      source: this.#source,
      view,
      canSeek: true,
      selectable: true,
      pages: EMPTY,
      onNavigate: () => {},
      list: this.#list,
      onList: (list: ListState) => {
        this.#list = list;
        this.#render();
      },
      memory: FRESH_MEMORY,
      onMemory: () => {},
      width: null,
      onWidth: () => {},
      onClose: () => {},
    });
    const picture = createElement("div", { style: { flex: "1 1 0" } });
    this.root.render(
      createElement(
        "div",
        { style: { display: "contents" } },
        picture,
        this.#open ? panel : null,
      ) as ReactElement,
    );
  }

  /** The element the rows scroll in: the listbox's nearest ancestor that scrolls vertically. */
  get scroller(): HTMLElement {
    let element = this.host.querySelector('[role="listbox"]')!.parentElement!;
    while (getComputedStyle(element).overflowY !== "auto") element = element.parentElement!;
    return element;
  }

  /** Whether rows of the scenario's list are drawn and none waits for its page: a list of another size is never accepted. */
  get loaded(): boolean {
    const rows = this.host.querySelectorAll('[role="option"]');
    return (
      rows.length > 0 &&
      Number(rows[0].getAttribute("aria-setsize")) === this.scenario.rows &&
      !this.host.querySelector('[role="option"][aria-busy="true"]')
    );
  }

  close(): void {
    this.root.unmount();
    this.host.remove();
  }
}

/** The inspector over an object with `scenario.rows` fields, every group open, in the 1280 x 720 viewer. */
class InspectorRig {
  readonly host = document.createElement("div");
  readonly root: Root;
  readonly #fields: FieldWire[];
  #memory: InspectorMemory = { pins: [], folded: new Set<FieldGroupWire>() };

  constructor(rows: number) {
    this.host.style.cssText =
      "position:relative;display:flex;width:1280px;height:720px;container-type:size";
    document.body.append(this.host);
    this.root = createRoot(this.host);
    const groups: FieldGroupWire[] = ["classification", "motion", "geometry"];
    this.#fields = Array.from({ length: rows }, (_, i) => ({
      group: i < 60 ? groups[i % 3] : "raw",
      path: i < 60 ? [`field${i}`] : ["data", `list`, String(i)],
      value: i % 3 === 0 ? i * 0.37 : `value of field ${i}`,
    }));
  }

  /** Renders the inspector; a pin or fold re-renders it with the memory it asks for. */
  show(): void {
    this.root.render(
      createElement(Inspector, {
        inspection: {
          state: "ready",
          selection: { lane: 0, source: 0, entity: "track-1" },
          summary: {
            entity: "track-1",
            className: "car",
            score: 0.9,
            confidence: 0.8,
            motion: null,
          },
          fields: this.#fields,
          truncated: false,
        },
        reach: { source: new FakeFiles(0), frame: 0, timeOf: () => undefined },
        memory: this.#memory,
        onMemory: (memory: InspectorMemory) => {
          this.#memory = memory;
          this.show();
        },
      }),
    );
  }

  get scroller(): HTMLElement {
    return this.host.querySelector<HTMLElement>('[role="list"]:last-of-type')!.parentElement!;
  }

  get rows(): NodeListOf<HTMLElement> {
    return this.host.querySelectorAll('[role="listitem"]');
  }

  close(): void {
    this.root.unmount();
    this.host.remove();
  }
}

const nextFrame = (): Promise<number> => new Promise((done) => requestAnimationFrame(done));
/** Resolves after the frame that follows has been painted. */
const painted = (): Promise<void> =>
  nextFrame().then(() => new Promise<void>((done) => setTimeout(done, 0)));

async function until(rig: Rig, ready: () => boolean): Promise<void> {
  if (ready()) return;
  await new Promise<void>((done, fail) => {
    const timer = setTimeout(() => {
      watch.disconnect();
      fail(new Error(`the ${rig.scenario.scope} list of ${rig.scenario.rows} rows never loaded`));
    }, 10_000);
    const watch = new MutationObserver(() => {
      if (!ready()) return;
      clearTimeout(timer);
      watch.disconnect();
      done();
    });
    watch.observe(rig.host, { childList: true, subtree: true, attributes: true });
  });
}

/** Calls `step` once per animation frame for `seconds`; the intervals between the frames. */
async function frames(seconds: number, step: (index: number) => void): Promise<number[]> {
  const intervals: number[] = [];
  let last = await nextFrame();
  const end = last + seconds * 1000;
  for (let index = 0; last < end; index++) {
    step(index);
    const now = await nextFrame();
    intervals.push(now - last);
    last = now;
  }
  return intervals;
}

const result = (
  scenario: TableScenario,
  workOf: string,
  work: number[],
  intervals: number[] = [],
): TableResult => ({
  scenario,
  workOf,
  work: summarize(work),
  intervals: summarize(intervals),
  late: intervals.filter((interval) => interval > LATE).length,
});

/** Chooses the File scope the way a click on its segment does. */
function chooseFile(rig: Rig): void {
  const label = [...rig.host.querySelectorAll("label")].find((l) => l.textContent === "File");
  label!.querySelector("input")!.click();
}

async function firstRows(scenario: TableScenario): Promise<TableResult> {
  const times: number[] = [];
  for (let rep = 0; rep < 10; rep++) {
    const rig = new Rig(scenario);
    let start = performance.now();
    if (scenario.scope === "Frame") rig.show(true);
    else {
      rig.show(true);
      await painted();
      start = performance.now();
      chooseFile(rig);
    }
    await until(rig, () => rig.loaded);
    await painted();
    times.push(performance.now() - start);
    rig.close();
  }
  return result(scenario, "to painted rows", times);
}

async function scroll(scenario: TableScenario): Promise<TableResult> {
  const rig = new Rig(scenario);
  rig.show(true);
  if (scenario.scope === "File") chooseFile(rig);
  await until(rig, () => rig.loaded);
  await painted();
  const { scroller } = rig;
  const row = scroller.querySelector<HTMLElement>('[role="option"]')!.offsetHeight;
  const latency: number[] = [];
  let scrolled = 0;
  const onScroll = () => (scrolled = performance.now());
  const rowsChanged = new MutationObserver(() => {
    if (scrolled > 0) latency.push(performance.now() - scrolled);
    scrolled = 0;
  });
  document.addEventListener("scroll", onScroll, true);
  rowsChanged.observe(scroller, { childList: true, subtree: true, attributes: true });
  const wrap = scroller.scrollHeight - scroller.clientHeight;
  // 12 rows a frame: every frame moves the window.
  const intervals = await frames(scenario.seconds, (index) => {
    scroller.scrollTop = (index * 12 * row) % wrap;
  });
  document.removeEventListener("scroll", onScroll, true);
  rowsChanged.disconnect();
  rig.close();
  if (latency.length === 0) throw new Error("scrolling never changed the rows drawn");
  return result(scenario, "scroll event to rows", latency, intervals);
}

async function resize(scenario: TableScenario): Promise<TableResult> {
  const rig = new Rig(scenario);
  rig.show(true);
  if (scenario.scope === "File") chooseFile(rig);
  await until(rig, () => rig.loaded);
  await painted();
  // No pointer is active for a synthetic event to capture.
  const proto = HTMLElement.prototype as { setPointerCapture: unknown };
  const capture = proto.setPointerCapture;
  proto.setPointerCapture = () => {};
  const divider = rig.host.querySelector<HTMLElement>('[role="separator"]')!;
  const x = divider.getBoundingClientRect().left;
  const send = (type: string, clientX: number) =>
    divider.dispatchEvent(
      new PointerEvent(type, {
        bubbles: true,
        pointerId: 1,
        pointerType: "mouse",
        clientX,
        buttons: type === "pointerup" ? 0 : 1,
      }),
    );
  send("pointerdown", x);
  const widths = new Set<string | null>();
  const work: number[] = [];
  const intervals = await frames(scenario.seconds, (index) => {
    const start = performance.now();
    send("pointermove", x + 60 * Math.sin(index / 10));
    // Layout is part of what a move costs.
    void rig.scroller.offsetHeight;
    work.push(performance.now() - start);
    widths.add(divider.getAttribute("aria-valuenow"));
  });
  send("pointerup", x);
  proto.setPointerCapture = capture;
  rememberedWidths("objects-frame").clear();
  rememberedWidths("objects-file").clear();
  rig.close();
  if (widths.size < 2) throw new Error("dragging the divider never changed the column width");
  return result(scenario, "pointer move and layout", work, intervals);
}

async function playback(scenario: TableScenario): Promise<TableResult> {
  const open = scenario.name === "playback open";
  const rig = new Rig(scenario);
  rig.show(open);
  await painted();
  const work: number[] = [];
  const intervals = await frames(scenario.seconds, (index) => {
    const start = performance.now();
    rig.show(open, index);
    work.push(performance.now() - start);
  });
  rig.close();
  return result(scenario, "render of a new frame", work, intervals);
}

async function inspectorFirstRows(scenario: TableScenario): Promise<TableResult> {
  const times: number[] = [];
  for (let rep = 0; rep < 10; rep++) {
    const rig = new InspectorRig(scenario.rows);
    const start = performance.now();
    flushSync(() => rig.show());
    await painted();
    times.push(performance.now() - start);
    if (rig.rows.length === 0) throw new Error("the inspector drew no rows");
    rig.close();
  }
  return result(scenario, "to painted rows", times);
}

async function inspectorScroll(scenario: TableScenario): Promise<TableResult> {
  const rig = new InspectorRig(scenario.rows);
  flushSync(() => rig.show());
  await painted();
  const { scroller } = rig;
  const row = rig.rows[0].offsetHeight;
  const latency: number[] = [];
  let scrolled = 0;
  const onScroll = () => (scrolled = performance.now());
  const rowsChanged = new MutationObserver(() => {
    if (scrolled > 0) latency.push(performance.now() - scrolled);
    scrolled = 0;
  });
  document.addEventListener("scroll", onScroll, true);
  rowsChanged.observe(scroller, { childList: true, subtree: true, attributes: true });
  const wrap = scroller.scrollHeight - scroller.clientHeight;
  const intervals = await frames(scenario.seconds, (index) => {
    scroller.scrollTop = (index * 12 * row) % wrap;
  });
  document.removeEventListener("scroll", onScroll, true);
  rowsChanged.disconnect();
  rig.close();
  if (latency.length === 0) throw new Error("scrolling never changed the rows drawn");
  return result(scenario, "scroll event to rows", latency, intervals);
}

async function inspectorPin(scenario: TableScenario): Promise<TableResult> {
  const rig = new InspectorRig(scenario.rows);
  flushSync(() => rig.show());
  await painted();
  const work: number[] = [];
  // Pin the first unpinned row, then unpin it from the pinned rows; a click ends with the page updated.
  for (const label of Array.from({ length: 20 }, (_, i) => (i % 2 === 0 ? "Pin " : "Unpin "))) {
    const button = rig.host.querySelector<HTMLElement>(`[data-act="pin"][aria-label^="${label}"]`)!;
    const start = performance.now();
    flushSync(() => button.click());
    void rig.scroller.offsetHeight;
    work.push(performance.now() - start);
    if (Boolean(rig.host.querySelector('section[aria-label="Pinned"]')) !== (label === "Pin "))
      throw new Error("the pin click did not change the pinned rows");
    await painted();
  }
  rig.close();
  return result(scenario, "pin click and layout", work);
}

const RUNNERS: Record<TableName, (scenario: TableScenario) => Promise<TableResult>> = {
  "first rows": firstRows,
  scroll,
  resize,
  "playback open": playback,
  "playback closed": playback,
  "inspector first rows": inspectorFirstRows,
  "inspector scroll": inspectorScroll,
  "inspector pin": inspectorPin,
};

/** Runs `scenario` in a fresh page-sized rig and removes it after. */
export const run = (scenario: TableScenario): Promise<TableResult> =>
  RUNNERS[scenario.name](scenario);

const f = (value: number): string => value.toFixed(2).padStart(7);

/** The table of results, with the budget each is held to: frame interval p95 <= one refresh at 100k rows. */
export function report(results: TableResult[]): string {
  const lines = [
    `${"scenario".padEnd(16)} ${"scope".padEnd(5)} ${"rows".padStart(6)} | ${"work".padEnd(24)} p50    p95    max | frame interval p50    p95 | late`,
  ];
  for (const { scenario: s, workOf, work, intervals, late } of results) {
    const driven = intervals.n > 0;
    lines.push(
      `${s.name.padEnd(16)} ${s.scope.padEnd(5)} ${String(s.rows).padStart(6)} | ${workOf.padEnd(24)}${f(work.p50)}${f(work.p95)}${f(work.max)} | ${driven ? `${f(intervals.p50)}${f(intervals.p95)}` : " ".repeat(14)} | ${driven ? `${late}/${intervals.n}` : ""}`,
    );
  }
  const budgeted = results.filter(
    ({ scenario: s }) =>
      (s.name === "scroll" || s.name === "resize") && s.scope === "File" && s.rows >= 100_000,
  );
  lines.push(
    "",
    `Budget: frame interval p95 <= ${BUDGET.toFixed(1)} ms scrolling and resizing at 100000 rows. late: intervals over ${LATE.toFixed(1)} ms.`,
    ...budgeted.map(
      ({ scenario: s, intervals }) =>
        `${s.name} ${s.scope}: ${intervals.p95 <= BUDGET ? "within" : "OVER"} (${intervals.p95.toFixed(2)} ms)`,
    ),
  );
  return lines.join("\n");
}

export const tables: TablesApi = { plan, run, report };
