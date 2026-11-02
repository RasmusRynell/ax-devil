/** The lanes' presentation on one glyph atlas: each lane's marks laid out for its cell, remembered from frame to frame. */

import type { LaneDrawing } from "../overlay/painter";
import {
  LanePresenter,
  pick,
  type LayoutView,
  type Region,
  type SampleView,
  type TextSource,
} from "../present";
import type { LaneFrame } from "./contract";
import { uiZoom } from "./uiZoom";

/** A lane to lay out: what it draws (`undefined`: nothing) and the displayed picture's size in CSS pixels. */
export type PlacedLane = {
  lane: number;
  frame: LaneFrame | undefined;
  view: { width: number; height: number; dpr: number };
  /** The part of the picture on screen, in the picture's CSS pixels, when zoom pushes the rest out of view. */
  visible?: Region | null;
};

/** A row of a lane's sample. */
export type FoundRow = { columns: SampleView; row: number };

const encoder = new TextEncoder();

/** A score or confidence to at most two decimals, such as `0.82`. */
function decimals(value: number): string {
  return String(Number(value.toFixed(2)));
}

/** What the hover chip says of a row: `"<id> <class>"` and `"score S · confidence C"`, leaving out what is missing. */
export function describeRow(
  columns: SampleView,
  row: number,
): { entity: string; label: string; detail: string } {
  const { strings } = columns;
  const entity = strings.string(columns.entityId[row]);
  const classId = columns.classId[row];
  const detail: string[] = [];
  const { score, confidence } = columns;
  if (!Number.isNaN(score[row])) detail.push(`score ${decimals(score[row])}`);
  if (!Number.isNaN(confidence[row])) detail.push(`confidence ${decimals(confidence[row])}`);
  return {
    entity,
    label: classId < 0 ? entity : `${entity} ${strings.string(classId)}`,
    detail: detail.join(" · "),
  };
}

export class LaneLayouts {
  readonly #text: TextSource;
  readonly #presenters: LanePresenter[] = [];
  /** How each lane was last laid out. */
  readonly #views: LayoutView[] = [];
  /** The row of the last entity looked up in a lane, valid while the lane's rows are as they were. */
  readonly #found = new Map<
    number,
    { evaluations: number; columns: SampleView; entity: string; row: number }
  >();

  constructor(text: TextSource) {
    this.#text = text;
  }

  /** Forgets the lanes from `count` on. */
  keep(count: number): void {
    this.#presenters.length = Math.min(this.#presenters.length, count);
    this.#views.length = Math.min(this.#views.length, count);
    for (const lane of this.#found.keys()) if (lane >= count) this.#found.delete(lane);
  }

  /**
   * What each placed lane draws, in the order given. A lane whose frame, style and size are as before costs nothing. If a
   * lane filled the atlas page and started a new one, the lanes laid out before it hold glyphs of the old page and are
   * laid out again on the new one, leaving out what does not fit. Lengths in px do not follow the page zoom, which is
   * read afresh each time.
   */
  layOut(placed: readonly PlacedLane[]): (LaneDrawing | undefined)[] {
    const zoom = uiZoom();
    const generation = this.#text.generation;
    const drawings = placed.map((lane) => this.#present(lane, zoom));
    if (this.#text.generation === generation) return drawings;
    this.#text.dropOverflow = true;
    try {
      return placed.map((lane) => this.#present(lane, zoom));
    } finally {
      this.#text.dropOverflow = false;
    }
  }

  /** The row of the lane's last sample under the point, given as fractions of its picture; the smallest box wins. */
  pick(lane: number, x: number, y: number): FoundRow | null {
    const presenter = this.#presenters[lane];
    const view = this.#views[lane];
    const columns = presenter?.sample;
    if (!columns || !view) return null;
    const row = pick(columns, presenter.rows, view, x * view.width, y * view.height);
    return row < 0 ? null : { columns, row };
  }

  /** The row of the lane's last sample that holds the entity, among the rows it kept. */
  find(lane: number, entity: string): FoundRow | null {
    const presenter = this.#presenters[lane];
    const columns = presenter?.sample;
    if (!columns) return null;
    const held = this.#found.get(lane);
    if (held?.evaluations === presenter.evaluations && held.entity === entity)
      return held.row < 0 ? null : { columns, row: held.row };
    const bytes = encoder.encode(entity);
    const { rows } = presenter;
    let row = -1;
    for (let i = 0; i < rows.count && row < 0; i++)
      if (columns.strings.equals(columns.entityId[rows.rows[i]], bytes)) row = rows.rows[i];
    this.#found.set(lane, { evaluations: presenter.evaluations, columns, entity, row });
    return row < 0 ? null : { columns, row };
  }

  #present({ lane, frame, view, visible }: PlacedLane, zoom: number): LaneDrawing | undefined {
    if (!frame) return undefined;
    const presenter = (this.#presenters[lane] ??= new LanePresenter(this.#text));
    const layout = { ...view, zoom, visible };
    this.#views[lane] = layout;
    const quads = presenter.present(
      {
        catalog: frame.catalog,
        sample: frame.columns,
        style: { hidden: frame.hidden, theme: frame.theme },
        mask: frame.classMask,
        filter: frame.filter,
      },
      layout,
    );
    return { lane, quads, version: presenter.layouts, view, opacity: frame.opacity };
  }
}
