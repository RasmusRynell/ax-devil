import type { Position } from "../catalog/locate";

/** A half-open range of UTF-16 code units inside an expression string. */
export type Span = readonly [start: number, end: number];

/** The categories of compile error; each test and each editor message keys on one. */
export type DiagnosticCode =
  | "syntax" // The text does not parse.
  | "unknown-name" // A field, variable, function or key that does not exist.
  | "type" // A value of the wrong type.
  | "arity" // A function called with the wrong number of arguments.
  | "constant" // An argument that must be a literal is not.
  | "length" // A length combination that cannot resolve at layout.
  | "ramp" // Color stops that are not strictly increasing.
  | "cycle" // Variables that depend on each other.
  | "infer" // A type that nothing determines.
  | "schema" // A document that is not catalog schema 4.
  | "unknown-key" // A key the closed object does not have.
  | "missing-key" // A required key that is absent.
  | "value" // A well-typed value outside its allowed set.
  | "duplicate-key" // An object that names one key twice; JSON.parse would silently keep the last.
  | "skipped" // A mark that could not be drawn for a row of an example, found by `catalog check`.
  | "limit" // An expression or catalog larger or more deeply nested than compiling accepts.
  | "internal"; // A failure of the compiler itself; reported instead of thrown.

/** One compile problem, located by the JSON pointer of the string and the span inside it. */
export interface Diagnostic {
  readonly severity: "error" | "warning";
  readonly code: DiagnosticCode;
  readonly pointer: string;
  readonly span?: Span;
  /** The document position, when the pointer cannot name it (a duplicate key's value is not what `JSON.parse` keeps). */
  readonly at?: Position;
  readonly message: string;
}

/** Where an expression came from: its text, its JSON pointer, and whether spans point into a document string. */
export interface Source {
  readonly text: string;
  readonly pointer: string;
  /** False for numbers and booleans written directly in the document: there is no string to point into. */
  readonly spans: boolean;
}

/** Thrown after a problem is recorded, to leave the current expression; compile time only, caught by `attempt`. */
class Aborted extends Error {}

/** Collects every problem of a compile so that all are reported together. */
export class Diagnostics {
  readonly list: Diagnostic[] = [];

  /** Record a problem and keep going. */
  add(
    code: DiagnosticCode,
    src: Pick<Source, "pointer" | "spans">,
    span: Span | null,
    message: string,
  ): void {
    const diagnostic: Diagnostic = { severity: "error", code, pointer: src.pointer, message };
    this.list.push(span && src.spans ? { ...diagnostic, span } : diagnostic);
  }

  /** Record a problem and leave the current expression. */
  fail(
    code: DiagnosticCode,
    src: Pick<Source, "pointer" | "spans">,
    span: Span | null,
    message: string,
  ): never {
    this.add(code, src, span, message);
    throw new Aborted();
  }

  /** Run one expression's step; a recorded failure leaves it and returns null. */
  attempt<T>(step: () => T): T | null {
    try {
      return step();
    } catch (error) {
      if (error instanceof Aborted) return null;
      throw error;
    }
  }
}

/** The diagnostic for a compile that failed unexpectedly, so no entry point throws at its caller. */
export function internalError(error: unknown, pointer = ""): Diagnostic {
  const detail = error instanceof Error ? `: ${error.message}` : "";
  return {
    severity: "error",
    code: "internal",
    pointer,
    message: `internal error compiling${detail}`,
  };
}

function distance(a: string, b: string): number {
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(
        previous[j] + 1,
        row[j - 1] + 1,
        previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    previous = row;
  }
  return previous[b.length];
}

/** "; did you mean 'x'?" for the closest candidate within two edits, else an empty string. */
export function didYouMean(name: string, candidates: Iterable<string>): string {
  let best = "";
  let bestDistance = 3;
  for (const candidate of candidates) {
    const d = distance(name, candidate);
    if (d < bestDistance) {
      best = candidate;
      bestDistance = d;
    }
  }
  return best ? `; did you mean '${best}'?` : "";
}
