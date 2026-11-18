import { readFileSync } from "node:fs";
import { compileCatalog } from "../catalog/compile";
import { FEATURES, type CompiledCatalog } from "../catalog/types";
import { type SampleView, readSample } from "../columns";
import { EvalReport } from "../evaluate/report";
import { evaluate } from "../evaluate/evaluate";
import { RowSet, filterRows } from "../filter/filter";
import { Layouter } from "../layout/layout";
import type { LayoutView } from "../layout/sink";
import { MarkBuffer } from "../marks/buffer";
import { programOf } from "../marks/plan";
import { FakeText } from "../text/fakeSource";
import type { TextSource } from "../text/source";
import { CanonicalSink, type Rows } from "./sink";

/** The repository root, as a URL. */
export const ROOT = new URL("../../../../", import.meta.url);

/** The built-in v4 catalog `name`, compiled; throws with the diagnostics when it does not compile. */
export function builtIn(name: string): CompiledCatalog {
  const result = compileCatalog(
    readFileSync(new URL(`assets/catalogs/${name}.v4.json`, ROOT), "utf8"),
  );
  if (!result.ok)
    throw new Error(
      `${name}: ${result.diagnostics.map((d) => `${d.pointer} ${d.message}`).join("; ")}`,
    );
  return result.catalog;
}

/** The bit mask hiding the named features. */
export function hiddenBits(names: readonly string[]): number {
  return names.reduce(
    (bits, name) => bits | (1 << FEATURES.indexOf(name as (typeof FEATURES)[number])),
    0,
  );
}

/** The golden Sample Item of an oracle sheet or MOT scene. */
export function golden(name: string) {
  return readSample(
    new Uint8Array(readFileSync(new URL(`fixtures/oracle/items/${name}.bin`, ROOT))),
  );
}

/** The oracle view: 1280 x 720 CSS pixels at device ratio 1 and no UI zoom. */
export const ORACLE_VIEW: LayoutView = { width: 1280, height: 720, dpr: 1, zoom: 1 };

/** Evaluate and lay out the golden `item` with `catalog` into oracle rows, with the oracle's font metrics. */
export function render(
  catalog: CompiledCatalog,
  item: string,
  hidden: number,
): { rows: Rows; report: EvalReport } {
  return renderSample(catalog, golden(item), hidden);
}

/** Evaluate and lay out `cols` with `catalog` into oracle rows; `view` is the oracle's unless given. */
export function renderSample(
  catalog: CompiledCatalog,
  cols: SampleView,
  hidden: number,
  view: LayoutView = ORACLE_VIEW,
  text: TextSource = new FakeText(),
): { rows: Rows; report: EvalReport } {
  const set = filterRows(
    cols,
    null,
    { enabled: new Uint32Array(cols.maskWords), options: 0 },
    new RowSet(),
  );
  const marks = new MarkBuffer();
  const report = new EvalReport();
  evaluate(cols, set, catalog, { hidden, theme: null }, marks, report);
  const plans = programOf(catalog).marks;
  const sink = new CanonicalSink(
    { marks, family: (record) => plans[marks.words[record * 12]].family },
    view.width,
    view.height,
  );
  new Layouter(text).run(marks, catalog, cols, view, sink, report);
  return { rows: sink.rows, report };
}
