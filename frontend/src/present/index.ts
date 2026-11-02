export {
  compileCatalog,
  compileCatalogDocument,
  CATALOG_SCHEMA,
  type CatalogResult,
} from "./catalog/compile";
export { locate, type Position } from "./catalog/locate";
export { computeVars, themeIndex } from "./catalog/program";
export { FEATURES, type CompiledCatalog, type LayerSpec, type MarkSpec } from "./catalog/types";
export type { Diagnostic, DiagnosticCode } from "./expr/diagnostic";
export {
  compileFilter,
  filterRows,
  RowSet,
  type ClassMask,
  type CompiledFilter,
  type FilterResult,
} from "./filter/filter";
export { readSample, SampleFormatError, type SampleView } from "./columns";
export { CodePoints, codePointString } from "./expr/codePoints";
export { type EvalStyle, evaluate } from "./evaluate/evaluate";
export { EvalReport } from "./evaluate/report";
export { Layouter } from "./layout/layout";
export type { LayoutView, Region } from "./layout/sink";
export { LanePresenter, type LaneInput } from "./lane";
export { pick, type Pick } from "./pick/pick";
export { MarkBuffer } from "./marks/buffer";
export { LaneQuads } from "./quads/lane";
export { QuadSink } from "./quads/quadSink";
export { AtlasFull, type FontHandle, type Glyph, type TextSource } from "./text/source";
