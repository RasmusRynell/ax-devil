import { CanonicalSink } from "../canonical/sink";
import { readSample } from "../columns";
import type { Diagnostic } from "../expr/diagnostic";
import { evaluate } from "../evaluate/evaluate";
import { EvalReport } from "../evaluate/report";
import { RowSet, filterRows } from "../filter/filter";
import { Layouter } from "../layout/layout";
import type { LayoutView } from "../layout/sink";
import { MarkBuffer } from "../marks/buffer";
import { programOf } from "../marks/plan";
import { FakeText } from "../text/fakeSource";
import type { CompiledCatalog } from "./types";

/** An example `catalog check` draws the catalog on: its name and the bytes of its Sample Item. */
export interface Sheet {
  readonly name: string;
  readonly item: Uint8Array;
}

/** What drawing the examples found: the marks of the catalog, and a problem for each mark skipped. */
export interface Exercised {
  readonly marks: number;
  readonly skipped: readonly Diagnostic[];
}

/** The size sheets are laid out at. */
const SHEET_VIEW: LayoutView = { width: 1280, height: 720, dpr: 1, zoom: 1 };

/**
 * Evaluate and lay out every mark of `catalog` on the Sample Item of `sheet`, once with no theme and once per theme
 * the catalog declares. Nothing is rasterized and the text metrics are fixed, so it needs no window; a mark skipped
 * because a field it needs is null or its lengths do not work is a problem at the mark.
 */
export function exercise(catalog: CompiledCatalog, sheet: Sheet): Exercised {
  const cols = readSample(sheet.item);
  const marks = catalog.layers.flatMap((layer) => layer.marks);
  const skipped: Diagnostic[] = [];
  const themes: (string | null)[] = [null, ...catalog.themes];
  for (const theme of themes) {
    const report = new EvalReport();
    const buffer = new MarkBuffer();
    const set = filterRows(
      cols,
      null,
      { enabled: new Uint32Array(cols.maskWords), options: 0 },
      new RowSet(),
    );
    evaluate(cols, set, catalog, { hidden: 0, theme }, buffer, report);
    const plans = programOf(catalog).marks;
    const sink = new CanonicalSink(
      { marks: buffer, family: (record) => plans[buffer.words[record * 12]].family },
      SHEET_VIEW.width,
      SHEET_VIEW.height,
    );
    new Layouter(new FakeText()).run(buffer, catalog, cols, SHEET_VIEW, sink, report);
    for (const mark of marks) {
      const rows = report.skipped[mark.index] ?? 0;
      if (rows === 0) continue;
      const where = theme === null ? sheet.name : `${sheet.name} with theme '${theme}'`;
      skipped.push({
        severity: "error",
        code: "skipped",
        pointer: mark.pointer,
        message: `${where}: skipped for ${rows} ${rows === 1 ? "row" : "rows"}`,
      });
    }
  }
  return { marks: marks.length, skipped };
}
