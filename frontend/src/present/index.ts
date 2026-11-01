export {
  compileCatalog,
  compileCatalogDocument,
  CATALOG_SCHEMA,
  type CatalogResult,
} from "./catalog/compile";
export { locate, type Position } from "./catalog/locate";
export { computeVars, themeIndex } from "./catalog/program";
export type { CompiledCatalog, LayerSpec, MarkSpec } from "./catalog/types";
export type { Diagnostic, DiagnosticCode } from "./expr/diagnostic";
export {
  compileFilter,
  filterRows,
  RowSet,
  type ClassMask,
  type CompiledFilter,
  type FilterResult,
} from "./filter/filter";
export type { SampleView } from "./expr/sampleShape";
