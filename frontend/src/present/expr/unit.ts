import { EvalContext } from "./context";
import { Diagnostics } from "./diagnostic";
import { TypeVar } from "./types";

/** A named per-row value of a catalog; the checker only needs its type. */
export interface VarInfo {
  readonly name: string;
  readonly type: TypeVar;
}

/** What one catalog or filter compiles into: the shared context, the problems found and the open types. */
export class ExprUnit {
  readonly ctx = new EvalContext();
  readonly diag = new Diagnostics();
  /** One type per attribute name, so every use of `attr.x` agrees. */
  readonly attrTypes = new Map<string, TypeVar>();
  readonly vars = new Map<string, VarInfo>();
}
