import type { Diagnostic } from "../expr/diagnostic";
import { pointerTo } from "./document";

/** A 1-based position in a document. */
export interface Position {
  readonly line: number;
  readonly column: number;
}

const WHITESPACE = new Set([" ", "\t", "\n", "\r"]);
const ESCAPES = new Set(["\\", '"', "/", "b", "f", "n", "r", "t"]);

class Scanner {
  /** The offset where each line starts, built on first use. */
  private starts: number[] | null = null;

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

  /** The index after the value starting at `i`. Counts the nesting depth rather than recursing, so no depth overflows the stack. */
  skipValue(i: number): number {
    const c = this.text[i];
    if (c === '"') return this.skipString(i);
    let at = i;
    if (c !== "{" && c !== "[") {
      while (at < this.text.length && !",}] \t\n\r".includes(this.text[at])) at++;
      return at;
    }
    let depth = 0;
    while (at < this.text.length) {
      const d = this.text[at];
      if (d === '"') {
        at = this.skipString(at);
        continue;
      }
      at++;
      if (d === "{" || d === "[") depth++;
      else if ((d === "}" || d === "]") && --depth === 0) break;
    }
    return at;
  }

  /**
   * The index of the child of the value at `i` named by `key` (an object member or an array index), or -1. Of
   * members that share a name it is the last, the one `JSON.parse` keeps.
   */
  child(i: number, key: string): number {
    const open = this.text[i];
    let at = this.skipSpace(i + 1);
    let index = 0;
    let found = -1;
    while (at < this.text.length && this.text[at] !== "}" && this.text[at] !== "]") {
      let name = String(index);
      if (open === "{") {
        const end = this.skipString(at);
        name = JSON.parse(this.text.slice(at, end)) as string;
        at = this.skipSpace(end) + 1;
      }
      const value = this.skipSpace(at);
      if (name === key) found = value;
      at = this.skipSpace(this.skipValue(value));
      if (this.text[at] === ",") at = this.skipSpace(at + 1);
      index++;
    }
    return found;
  }

  /** The name of the member whose key string starts at `at`. */
  keyAt(at: number, end: number): string {
    const raw = this.text.slice(at, end);
    return raw.includes("\\") ? (JSON.parse(raw) as string) : raw.slice(1, -1);
  }

  /**
   * Every object member name that occurs more than once in the valid JSON text, once per object, with the position
   * of the last occurrence's value. One pass, and no recursion, so no nesting depth can overflow the stack. It stops
   * at the first `MAX_DUPLICATES + 1`, so a hostile document costs a bounded number of pointers.
   */
  duplicates(): DuplicateKey[] {
    interface Frame {
      parent: Frame | null;
      closer: string;
      /** The member name or array index of the child being read. */
      current: string | number;
      /** Per name: how often it occurred and where its last value starts; objects only. */
      seen: Map<string, { count: number; at: number }> | null;
    }
    const found: DuplicateKey[] = [];
    let top: Frame | null = null;
    const pointerOf = (frame: Frame): string => {
      const keys: (string | number)[] = [];
      for (let up: Frame | null = frame; up; up = up.parent) keys.push(up.current);
      return keys
        .reverse()
        .map((key) => pointerTo("", key))
        .join("");
    };
    /** Reads the member of `frame` that starts at `i` and returns where its value starts. */
    const enter = (frame: Frame, i: number): number => {
      if (!frame.seen) {
        frame.current = Number(frame.current) + 1;
        return i;
      }
      const end = this.skipString(i);
      const name = this.keyAt(i, end);
      const value = this.skipSpace(this.skipSpace(end) + 1);
      const entry = frame.seen.get(name);
      if (entry) {
        entry.count++;
        entry.at = value;
      } else {
        frame.seen.set(name, { count: 1, at: value });
      }
      frame.current = name;
      return value;
    };
    /** Closes `frame`; true once more than `MAX_DUPLICATES` are found, which ends the scan. */
    const close = (frame: Frame): boolean => {
      for (const [key, { count, at }] of frame.seen ?? []) {
        frame.current = key;
        if (count < 2) continue;
        found.push({ pointer: pointerOf(frame), key, ...this.position(at) });
        if (found.length > MAX_DUPLICATES) return true;
      }
      top = frame.parent;
      return false;
    };
    let at = this.skipSpace(0);
    for (;;) {
      const c = this.text[at];
      if (c === "{" || c === "[") {
        const frame: Frame = {
          parent: top,
          closer: c === "{" ? "}" : "]",
          current: -1,
          seen: c === "{" ? new Map() : null,
        };
        top = frame;
        at = this.skipSpace(at + 1);
        if (this.text[at] !== frame.closer) {
          at = enter(frame, at);
          continue;
        }
        at++;
        if (close(frame)) return found;
      } else {
        at = this.skipValue(at);
      }
      for (;;) {
        if (!top) return found;
        at = this.skipSpace(at);
        if (this.text[at] === ",") {
          at = enter(top, this.skipSpace(at + 1));
          break;
        }
        at++;
        if (close(top)) return found;
      }
    }
  }

  /** The 1-based line and column of the text offset `at`. */
  position(at: number): Position {
    if (!this.starts) {
      this.starts = [0];
      for (let i = this.text.indexOf("\n"); i >= 0; i = this.text.indexOf("\n", i + 1))
        this.starts.push(i + 1);
    }
    let low = 0;
    let high = this.starts.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if (this.starts[mid] <= at) low = mid;
      else high = mid - 1;
    }
    return { line: low + 1, column: at - this.starts[low] + 1 };
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

/** Where a diagnostic is in the document text: its own position when it has one, else the value its pointer names and its span inside that string. */
export function locate(
  diagnostic: Pick<Diagnostic, "pointer" | "span" | "at">,
  text: string,
): Position | null {
  if (diagnostic.at) return diagnostic.at;
  const scanner = new Scanner(text);
  let at = scanner.skipSpace(0);
  const keys = diagnostic.pointer === "" ? [] : diagnostic.pointer.split("/").slice(1);
  for (const key of keys) {
    if (text[at] !== "{" && text[at] !== "[") return null;
    at = scanner.child(at, key.replaceAll("~1", "/").replaceAll("~0", "~"));
    if (at < 0) return null;
  }
  if (diagnostic.span && text[at] === '"') at = scanner.inString(at, diagnostic.span[0]);
  return scanner.position(at);
}

/** An object member name that occurs more than once in one object. */
export interface DuplicateKey extends Position {
  /** The pointer to the last occurrence, the one `JSON.parse` keeps. */
  readonly pointer: string;
  readonly key: string;
}

/** How many duplicate keys are listed; the scan stops one past it, which is how a caller knows there are more. */
export const MAX_DUPLICATES = 256;

/** The duplicated member names in the valid JSON `text`, at most `MAX_DUPLICATES + 1`; call it once per document revision. */
export function duplicateKeys(text: string): DuplicateKey[] {
  return new Scanner(text).duplicates();
}
