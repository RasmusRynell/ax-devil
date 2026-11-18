import { computeVars, themeIndex } from "../catalog/program";
import type { CompiledCatalog } from "../catalog/types";
import type { SampleView } from "../columns";
import type { RowSet } from "../filter/filter";
import { type MarkBuffer, RELATION_BIT, RowFaults } from "../marks/buffer";
import { programOf } from "../marks/plan";
import type { EvalReport } from "./report";
import { MarkWriter } from "./write";

/** What a lane chooses that evaluation needs. */
export interface EvalStyle {
  /** Bit i set hides the layers of `FEATURES[i]`. */
  readonly hidden: number;
  /** A theme of the catalog, or null for its own defaults. */
  readonly theme: string | null;
}

const writer = new MarkWriter();
const faults = new RowFaults();

/**
 * Evaluate `catalog` over the kept rows of `sample` into `out`, layer after layer, rows in sample order within a layer.
 * A mark whose needed field is null, or whose lengths do not work, fails its row: every mark that entity row (or
 * relation) has in every shown layer is left out, and the row is counted once in `report` against the first failing
 * mark. Other rows are unaffected. Nothing is allocated per row: the variables of every row are computed once into
 * the catalog's registers, and the marks are written into `out`'s typed arrays.
 */
export function evaluate(
  sample: SampleView,
  rows: RowSet,
  catalog: CompiledCatalog,
  style: EvalStyle,
  out: MarkBuffer,
  report: EvalReport,
): void {
  const program = programOf(catalog);
  out.clear();
  report.clear(program.marks.length);
  catalog.ctx.bind(sample);
  report.mismatched = catalog.ctx.mismatched;
  writer.start(sample, out);
  faults.clear();
  const theme = themeIndex(catalog, style.theme);
  const kept = rows.rows;
  for (let i = 0; i < rows.count; i++) computeVars(catalog, kept[i], theme);
  for (const layer of program.layers) {
    const shown = !layer.never && (style.hidden & layer.featureBit) === 0;
    if (shown && layer.relations) {
      for (let i = 0; i < rows.relationCount; i++) {
        const relation = rows.relations[i];
        const word = (relation | RELATION_BIT) >>> 0;
        if (faults.failed(word)) continue;
        if (layer.when !== null && layer.when.fn(relation) !== 1) continue;
        for (const mark of layer.marks) {
          if (writer.write(mark, relation, true)) continue;
          if (faults.fail(word)) report.skipped[mark.index]++;
          break;
        }
      }
    } else if (shown) {
      for (let i = 0; i < rows.count; i++) {
        const row = kept[i];
        if (faults.failed(row)) continue;
        if (layer.when !== null && layer.when.fn(row) !== 1) continue;
        for (const mark of layer.marks) {
          if (writer.write(mark, row, false)) continue;
          if (faults.fail(row)) report.skipped[mark.index]++;
          break;
        }
      }
    }
    out.endLayer(layer.index);
  }
  if (faults.any) out.compact(faults);
}
