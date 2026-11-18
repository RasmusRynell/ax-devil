import type { Diagnostics, Source, Span } from "./diagnostic";

/** One lexical token of an expression. */
export interface Token {
  readonly kind: "num" | "str" | "color" | "ident" | "op" | "end";
  /** The operator or identifier text; the decoded string for `str`. */
  readonly text: string;
  /** The number, or the packed 0xRRGGBBAA color. */
  readonly value: number;
  /** A number written with the `px` unit. */
  readonly px: boolean;
  readonly span: Span;
}

const OPERATORS = [
  "||",
  "&&",
  "==",
  "!=",
  "<=",
  ">=",
  "??",
  "!",
  "<",
  ">",
  "+",
  "-",
  "*",
  "/",
  "(",
  ")",
  "[",
  "]",
  ",",
  ".",
];

const isDigit = (c: string): boolean => c >= "0" && c <= "9";
const isIdentStart = (c: string): boolean =>
  (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_";
const isIdent = (c: string): boolean => isIdentStart(c) || isDigit(c);
const isHex = (c: string): boolean =>
  isDigit(c) || (c >= "a" && c <= "f") || (c >= "A" && c <= "F");

/** Pack `#rrggbb` or `#rrggbbaa` digits into 0xRRGGBBAA as a non-negative number. */
export function packColor(hex: string): number {
  const value = Number.parseInt(hex.length === 6 ? `${hex}ff` : hex, 16);
  return value;
}

/** Split an expression into tokens; the first lexical problem is recorded and ends the expression. */
export function tokenize(src: Source, diag: Diagnostics): Token[] {
  const text = src.text;
  const tokens: Token[] = [];
  let i = 0;
  const token = (kind: Token["kind"], start: number, value = 0, str = "", px = false): void => {
    tokens.push({ kind, text: str, value, px, span: [start, i] });
  };
  while (i < text.length) {
    const c = text[i];
    const start = i;
    if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      i++;
    } else if (isDigit(c)) {
      while (isDigit(text[i] ?? "")) i++;
      if (text[i] === "." && isDigit(text[i + 1] ?? "")) {
        i++;
        while (isDigit(text[i] ?? "")) i++;
      }
      const value = Number(text.slice(start, i));
      const unit = i;
      while (isIdent(text[i] ?? "")) i++;
      const suffix = text.slice(unit, i);
      if (suffix !== "" && suffix !== "px") {
        diag.fail(
          "syntax",
          src,
          [unit, i],
          `unknown unit '${suffix}'; lengths are written like 1.5px`,
        );
      }
      token("num", start, value, "", suffix === "px");
    } else if (c === "'" || c === '"') {
      i++;
      let value = "";
      while (i < text.length && text[i] !== c) {
        if (text[i] === "\\") {
          const next = text[i + 1];
          if (next !== "'" && next !== '"' && next !== "\\") {
            diag.fail(
              "syntax",
              src,
              [i, Math.min(i + 2, text.length)],
              "unknown escape; use \\' \\\" or \\\\",
            );
          }
          value += next;
          i += 2;
        } else {
          value += text[i++];
        }
      }
      if (i >= text.length) diag.fail("syntax", src, [start, text.length], "unterminated string");
      i++;
      token("str", start, 0, value);
    } else if (c === "#") {
      i++;
      while (isHex(text[i] ?? "")) i++;
      const digits = text.slice(start + 1, i);
      if ((digits.length !== 6 && digits.length !== 8) || isIdent(text[i] ?? "")) {
        while (isIdent(text[i] ?? "")) i++;
        diag.fail("syntax", src, [start, i], "a color is #rrggbb or #rrggbbaa");
      }
      token("color", start, packColor(digits));
    } else if (isIdentStart(c)) {
      while (isIdent(text[i] ?? "")) i++;
      token("ident", start, 0, text.slice(start, i));
    } else {
      const op = OPERATORS.find((candidate) => text.startsWith(candidate, i));
      if (op === undefined) diag.fail("syntax", src, [i, i + 1], `unexpected character '${c}'`);
      i += op.length;
      token("op", start, 0, op);
    }
  }
  tokens.push({ kind: "end", text: "", value: 0, px: false, span: [text.length, text.length] });
  return tokens;
}
