import type { CompiledCatalog } from "./catalog/types";
import type { SampleView } from "./columns";
import { type EvalStyle, evaluate } from "./evaluate/evaluate";
import { EvalReport } from "./evaluate/report";
import { type ClassMask, type CompiledFilter, RowSet, filterRows } from "./filter/filter";
import { Layouter } from "./layout/layout";
import type { LayoutView, Region } from "./layout/sink";
import { MarkBuffer } from "./marks/buffer";
import { LaneQuads } from "./quads/lane";
import { QuadSink } from "./quads/quadSink";
import type { TextSource } from "./text/source";

/** What a lane shows: the data and everything the viewer chose about how to filter and style it. */
export interface LaneInput {
  catalog: CompiledCatalog;
  /** The decoded item. A new object is new data; the same object is the same data. */
  sample: SampleView;
  style: EvalStyle;
  mask: ClassMask;
  filter: CompiledFilter | null;
}

/** Whether `inner` lies inside `outer`, where null is the whole image. */
function contains(outer: Region | null, inner: Region | null): boolean {
  if (outer === null) return true;
  return (
    inner !== null &&
    outer.left <= inner.left &&
    outer.top <= inner.top &&
    outer.right >= inner.right &&
    outer.bottom >= inner.bottom
  );
}

/** `region` grown by half its size on every side, so a small pan stays inside what was laid out. */
function padded(region: Region, view: LayoutView): Region {
  const x = (region.right - region.left) / 2;
  const y = (region.bottom - region.top) / 2;
  return {
    left: Math.max(0, region.left - x),
    top: Math.max(0, region.top - y),
    right: Math.min(view.width, region.right + x),
    bottom: Math.min(view.height, region.bottom + y),
  };
}

/**
 * One lane's presentation with its memory: filter and evaluate again only when the data, catalog or style changed, lay
 * out again only when additionally the displayed size, device ratio, zoom or atlas page changed or a pan reaches past
 * what was laid out. Pan and opacity are not inputs at all: the painter applies them to the quads as they are.
 */
export class LanePresenter {
  /** The quads of the last `present`, valid until the next. */
  readonly quads = new LaneQuads();
  /** What evaluation and layout left out. */
  readonly report = new EvalReport();
  /**
   * The rows the lane's class mask and filter kept, as of the last evaluation. What features hide or the catalog does
   * not draw is not removed here, so those rows can still be picked and inspected.
   */
  readonly rows = new RowSet();
  /** Evaluations and layouts run, to see what a call cost. */
  evaluations = 0;
  layouts = 0;
  private readonly marks = new MarkBuffer();
  private readonly sink = new QuadSink(this.quads);
  private readonly layouter: Layouter;
  /** What the marks were evaluated from, copied: the caller may change its input in place. */
  private catalogUid = -1;
  private evaluated: SampleView | null = null;
  private hidden = 0;
  private theme: string | null = null;
  private filter: CompiledFilter | null = null;
  private enabled = new Uint32Array(0);
  private options = 0;
  private skipped = new Uint32Array(0);
  private laid: { view: LayoutView; region: Region | null; generation: number } | null = null;

  constructor(private readonly text: TextSource) {
    this.layouter = new Layouter(text);
  }

  /** The sample the rows and quads were made from, null before the first `present`. */
  get sample(): SampleView | null {
    return this.evaluated;
  }

  /** The lane's quads for `input` shown as `view`. */
  present(input: LaneInput, view: LayoutView): LaneQuads {
    if (!this.sameData(input)) {
      this.evaluate(input);
      this.laid = null;
    }
    const laid = this.laid;
    if (
      laid === null ||
      laid.generation !== this.text.generation ||
      laid.view.width !== view.width ||
      laid.view.height !== view.height ||
      laid.view.dpr !== view.dpr ||
      laid.view.zoom !== view.zoom ||
      !contains(laid.region, view.visible ?? null)
    )
      this.layout(input, view);
    return this.quads;
  }

  private sameData(input: LaneInput): boolean {
    return (
      this.catalogUid === input.catalog.uid &&
      this.evaluated === input.sample &&
      this.hidden === input.style.hidden &&
      this.theme === input.style.theme &&
      this.filter === input.filter &&
      this.options === input.mask.options &&
      this.enabled.length === input.mask.enabled.length &&
      this.enabled.every((word, i) => word === input.mask.enabled[i])
    );
  }

  private evaluate(input: LaneInput): void {
    this.evaluations++;
    this.catalogUid = input.catalog.uid;
    this.evaluated = input.sample;
    this.hidden = input.style.hidden;
    this.theme = input.style.theme;
    this.filter = input.filter;
    this.enabled = Uint32Array.from(input.mask.enabled);
    this.options = input.mask.options;
    filterRows(input.sample, input.filter, input.mask, this.rows);
    evaluate(input.sample, this.rows, input.catalog, input.style, this.marks, this.report);
    this.skipped = Uint32Array.from(this.report.skipped);
  }

  private layout(input: LaneInput, view: LayoutView): void {
    this.layouts++;
    // Layout adds to the counters evaluation left.
    this.report.skipped.set(this.skipped);
    const region = view.visible ? padded(view.visible, view) : null;
    this.layouter.run(
      this.marks,
      input.catalog,
      input.sample,
      { ...view, visible: region },
      this.sink,
      this.report,
    );
    this.laid = { view, region, generation: this.text.generation };
  }
}
