import type { Span } from "./diagnostic";
import type { Ty } from "./types";

/** Whose row a field reads: the row itself, or the source or target entity of a relation. */
export type Side = "self" | "source" | "target";

interface Node {
  readonly span: Span;
  /** The node's own type; a variable until the uses of `attr`, variables and `null` fix it. */
  readonly ty: Ty;
}

/** A checked expression: names resolved, operands typed, ready to become closures. */
export type IR =
  | (Node & { readonly op: "num"; readonly value: number; readonly px: boolean })
  | (Node & { readonly op: "str"; readonly value: string })
  | (Node & { readonly op: "color"; readonly value: number })
  | (Node & { readonly op: "bool"; readonly value: boolean })
  | (Node & { readonly op: "null" })
  | (Node & { readonly op: "field"; readonly name: string; readonly side: Side })
  | (Node & { readonly op: "attr"; readonly name: string; readonly side: Side })
  | (Node & { readonly op: "var"; readonly name: string })
  | (Node & { readonly op: "list"; readonly items: readonly IR[] })
  | (Node & { readonly op: "call"; readonly name: string; readonly args: readonly IR[] })
  | (Node & { readonly op: "op"; readonly sym: string; readonly args: readonly IR[] });
