import type { Span } from "./diagnostic";

/** The syntax tree of one expression. */
export type Ast =
  | { readonly kind: "num"; readonly value: number; readonly px: boolean; readonly span: Span }
  | { readonly kind: "str"; readonly value: string; readonly span: Span }
  | { readonly kind: "color"; readonly value: number; readonly span: Span }
  | { readonly kind: "lit"; readonly value: "null" | "true" | "false"; readonly span: Span }
  | { readonly kind: "path"; readonly segments: readonly string[]; readonly span: Span }
  | {
      readonly kind: "call";
      readonly name: string;
      readonly args: readonly Ast[];
      readonly span: Span;
    }
  | { readonly kind: "list"; readonly items: readonly Ast[]; readonly span: Span }
  | { readonly kind: "unary"; readonly op: "-" | "!"; readonly arg: Ast; readonly span: Span }
  | {
      readonly kind: "binary";
      readonly op: string;
      readonly left: Ast;
      readonly right: Ast;
      readonly span: Span;
    }
  | { readonly kind: "in"; readonly left: Ast; readonly list: Ast; readonly span: Span };
