/** One scenario: a real `VideoRenderer` on a canvas, driven at a 30 fps cadence by animation frames. */

import type { Viewport } from "../lanes/contract";
import { type LaneCell, VideoRenderer } from "../playback/renderer";
import { FIT, ZOOM_STEP } from "../playback/viewport";
import { clearProbe, installProbes, probe } from "./probes";
import { summarize } from "./stats";
import { SIZES, catalogNamed, laneRing, videoFrame } from "./synthetic";
import { type Result, STAGES, type Scenario, type Stage } from "./types";

const PERIOD = 1000 / 30;
/** Steps of a play before the samples repeat. */
const RING = 6;
/** One presented frame in this many times the GL calls; the one after the first asks the GPU for its time. */
const PROBE_EVERY = 4;

const nextFrame = (): Promise<number> => new Promise((done) => requestAnimationFrame(done));

/** The lanes in a grid over the canvas, `null` when one lane has the whole canvas. */
function grid(lanes: number, width: number, height: number): LaneCell[] | null {
  if (lanes === 1) return null;
  const columns = Math.ceil(Math.sqrt(lanes));
  const rows = Math.ceil(lanes / columns);
  const [w, h] = [Math.floor(width / columns), Math.floor(height / rows)];
  return Array.from({ length: lanes }, (_, lane) => ({
    lane,
    rect: { x: (lane % columns) * w, y: Math.floor(lane / columns) * h, width: w, height: h },
  }));
}

/** GPU time of the whole step, from `EXT_disjoint_timer_query_webgl2` where the context has it. */
class GpuTimer {
  readonly ms: number[] = [];
  readonly #gl: WebGL2RenderingContext;
  readonly #ext: { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number } | null;
  readonly #pending: WebGLQuery[] = [];

  constructor(canvas: HTMLCanvasElement) {
    // The renderer made the context; asking again returns it.
    this.#gl = canvas.getContext("webgl2") as WebGL2RenderingContext;
    this.#ext = this.#gl.getExtension("EXT_disjoint_timer_query_webgl2");
  }

  get available(): boolean {
    return this.#ext !== null;
  }

  around(work: () => void): void {
    if (!this.#ext) return work();
    const query = this.#gl.createQuery();
    this.#gl.beginQuery(this.#ext.TIME_ELAPSED_EXT, query);
    work();
    this.#gl.endQuery(this.#ext.TIME_ELAPSED_EXT);
    this.#pending.push(query);
  }

  poll(): void {
    if (!this.#ext) return;
    const gl = this.#gl;
    const disjoint = gl.getParameter(this.#ext.GPU_DISJOINT_EXT);
    while (
      this.#pending.length > 0 &&
      gl.getQueryParameter(this.#pending[0], gl.QUERY_RESULT_AVAILABLE)
    ) {
      const query = this.#pending.shift() as WebGLQuery;
      if (!disjoint) this.ms.push(Number(gl.getQueryParameter(query, gl.QUERY_RESULT)) / 1e6);
      gl.deleteQuery(query);
    }
  }
}

/** Runs `scenario` and returns what it measured. Builds its own canvas and renderer and removes them after. */
export async function runScenario(scenario: Scenario): Promise<Result> {
  installProbes();
  const { width, height } = SIZES[scenario.resolution];
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.style.cssText = `display:block;width:${width / devicePixelRatio}px;height:${height / devicePixelRatio}px`;
  document.body.append(canvas);
  const renderer = new VideoRenderer(canvas);
  try {
    const catalog = catalogNamed(scenario.catalog);
    const ring = laneRing(catalog, scenario.lanes, scenario.objects, RING);
    const videos = [videoFrame(width, height, 0), videoFrame(width, height, 1)];
    probe.video = { width, height };
    renderer.setCells(grid(scenario.lanes, width, height));

    let viewport: Viewport = FIT;
    let zoomingIn = true;
    const wheelAt = { x: (width / devicePixelRatio) * 0.4, y: (height / devicePixelRatio) * 0.4 };
    const zoomStep = (): void => {
      const factor = (): number => (zoomingIn ? ZOOM_STEP : 1 / ZOOM_STEP);
      let next = renderer.zoomAt(viewport, factor(), wheelAt);
      if (next.zoom === viewport.zoom) {
        zoomingIn = !zoomingIn;
        next = renderer.zoomAt(viewport, factor(), wheelAt);
      }
      viewport = next;
      renderer.setViewport(next);
    };
    const step = (k: number): void => {
      if (scenario.mode === "play") renderer.present(videos[k % 2], ring[k % RING]);
      else if (scenario.mode === "paused") renderer.present(videos[0], ring[0]);
      else zoomStep();
    };

    // Warm up: the glyph atlas learns its glyphs, textures and buffers are allocated, the JIT settles.
    renderer.present(videos[0], ring[0]);
    for (let k = 0; k < 2 * RING + 4; k++) {
      step(k);
      if (k % 4 === 3) await nextFrame();
    }
    viewport = FIT;
    zoomingIn = true;
    renderer.setViewport(FIT);
    for (let i = 0; i < 5; i++) await nextFrame();

    const timer = new GpuTimer(canvas);
    const samples = Object.fromEntries(STAGES.map((stage) => [stage, [] as number[]])) as Record<
      Stage,
      number[]
    >;
    const longTasks: number[] = [];
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) longTasks.push(entry.duration);
    });
    observer.observe({ entryTypes: ["longtask"] });
    let dropped = 0;
    let accounted = 0;
    let presented = 0;
    const gaps: number[] = [];

    const present = (): void => {
      const probed = presented % PROBE_EVERY === PROBE_EVERY - 1;
      clearProbe();
      probe.cpu = true;
      probe.gl = probed;
      const start = performance.now();
      if (presented % PROBE_EVERY === 1) timer.around(() => step(presented));
      else step(presented);
      const spent = performance.now() - start;
      probe.cpu = false;
      probe.gl = false;
      const { ms } = probe;
      samples.filterEvaluate.push(ms.filterEvaluate);
      samples.layoutQuads.push(ms.layoutQuads);
      samples.marks.push(ms.filterEvaluate + ms.layoutQuads);
      if (probed)
        for (const stage of ["bufferUpload", "videoUpload", "atlasUpload", "draw"] as const)
          samples[stage].push(ms[stage]);
      else samples.present.push(spent);
      presented++;
    };

    const t0 = performance.now();
    await new Promise<void>((done) => {
      let last = t0;
      const tick = (): void => {
        const now = performance.now();
        if (now - t0 >= scenario.seconds * 1000) return done();
        if (now - last > PERIOD * 1.5) gaps.push(now - last);
        last = now;
        timer.poll();
        const due = Math.floor((now - t0) / PERIOD);
        if (due >= accounted) {
          dropped += due - accounted;
          accounted = due + 1;
          present();
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    for (const entry of observer.takeRecords()) longTasks.push(entry.duration);
    observer.disconnect();
    await nextFrame();
    timer.poll();

    return {
      scenario,
      stages: Object.fromEntries(
        STAGES.map((stage) => [stage, summarize(samples[stage])]),
      ) as Result["stages"],
      gpu: timer.available ? summarize(timer.ms) : null,
      presented,
      dropped,
      droppedPercent: (100 * dropped) / Math.max(1, presented + dropped),
      longTasks: {
        count: longTasks.length,
        totalMs: longTasks.reduce((sum, ms) => sum + ms, 0),
        maxMs: Math.max(0, ...longTasks),
      },
      rafGaps: { count: gaps.length, maxMs: Math.max(0, ...gaps) },
    };
  } finally {
    renderer.dispose();
    canvas.getContext("webgl2")?.getExtension("WEBGL_lose_context")?.loseContext();
    canvas.remove();
  }
}
