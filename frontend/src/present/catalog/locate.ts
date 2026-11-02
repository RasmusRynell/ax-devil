import type { Diagnostic } from "../expr/diagnostic";

/** A 1-based position in a document. */
export interface Position {
  readonly line: number;
  readonly column: number;
}

const WHITESPACE = new Set([" ", "\t", "\n", "\r"]);
const ESCAPES = new Set(["\\", '"', "/", "b", "f", "n", "r", "t"]);

class Scanner {
  constructor(private readonly text: string) {}

  skipSpace(i: number): number {
    while (i < this.text.length && WHITESPACE.has(this.text[i])) i++;
    return i;
  }

  /** The index after the string starting at `i`. */
  skipString(i: number): number {
    let at = i + 1;
    while (at < this.text.length && this.text[at] !== '"') at += this.text[at] === "\\" ? 2 : 1;
    return at + 1;
  }

  /** The index after the value starting at `i`. */
  skipValue(i: number): number {
    const c = this.text[i];
    if (c === '"') return this.skipString(i);
    if (c === "{" || c === "[") {
      let at = this.skipSpace(i + 1);
      while (at < this.text.length && this.text[at] !== (c === "{" ? "}" : "]")) {
        if (c === "{") at = this.skipSpace(this.skipString(at)) + 1;
        at = this.skipValue(this.skipSpace(at));
        at = this.skipSpace(at);
        if (this.text[at] === ",") at = this.skipSpace(at + 1);
      }
      return at + 1;
    }
    let at = i;
    while (at < this.text.length && !",}] \t\n\r".includes(this.text[at])) at++;
    return at;
  }

  /** The index of the child of the value at `i` named by `key` (an object member or an array index), or -1. */
  child(i: number, key: string): number {
    const open = this.text[i];
    let at = this.skipSpace(i + 1);
    let index = 0;
    while (at < this.text.length && this.text[at] !== "}" && this.text[at] !== "]") {
      let name = String(index);
      if (open === "{") {
        const end = this.skipString(at);
        name = JSON.parse(this.text.slice(at, end)) as string;
        at = this.skipSpace(end) + 1;
      }
      const value = this.skipSpace(at);
      if (name === key) return value;
      at = this.skipSpace(this.skipValue(value));
      if (this.text[at] === ",") at = this.skipSpace(at + 1);
      index++;
    }
    return -1;
  }

  /** The text offset of the character `decoded` code units into the string whose quote is at `quote`. */
  inString(quote: number, decoded: number): number {
    let at = quote + 1;
    for (let seen = 0; seen < decoded && at < this.text.length; seen++) {
      if (this.text[at] !== "\\") at += 1;
      else at += this.text[at + 1] === "u" ? 6 : ESCAPES.has(this.text[at + 1]) ? 2 : 1;
    }
    return at;
  }
}

/** Where a diagnostic is in the document text: the value its pointer names, and its span inside that string. */
export function locate(
  diagnostic: Pick<Diagnostic, "pointer" | "span">,
  text: string,
): Position | null {
  const scanner = new Scanner(text);
  let at = scanner.skipSpace(0);
  const keys = diagnostic.pointer === "" ? [] : diagnostic.pointer.split("/").slice(1);
  for (const key of keys) {
    if (text[at] !== "{" && text[at] !== "[") return null;
    at = scanner.child(at, key.replaceAll("~1", "/").replaceAll("~0", "~"));
    if (at < 0) return null;
  }
  if (diagnostic.span && text[at] === '"') at = scanner.inString(at, diagnostic.span[0]);
  const before = text.slice(0, at);
  const line = before.split("\n").length;
  return { line, column: at - (before.lastIndexOf("\n") + 1) + 1 };
}
