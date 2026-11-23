// Python's str.strip whitespace, including C0 separators and NEL, excluding JavaScript's BOM.
const SPACE = new Set([
  9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198,
  8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288,
]);
const DECIMAL = /\p{Decimal_Number}/u;

/** Python's numeric readers accept Unicode decimal digits, including consecutive ten-digit alphabets. */
function asciiDigit(digit: string): string {
  const point = digit.codePointAt(0)!;
  let start = point;
  while (DECIMAL.test(String.fromCodePoint(start - 1))) start -= 1;
  return String((point - start) % 10);
}

function stripped(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && SPACE.has(text.charCodeAt(start))) start += 1;
  while (end > start && SPACE.has(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(start, end);
}

const DIGITS = String.raw`\d(?:_?\d)*`;
const SYNTAX = {
  whole: new RegExp(`^[+-]?${DIGITS}$`),
  auto_or_whole: new RegExp(
    `^[+-]?(?:${DIGITS}(?:\\.(?:${DIGITS})?)?|\\.${DIGITS})(?:[eE][+-]?${DIGITS})?$`,
  ),
};

/** Parse Python-style integer or decimal input; leave invalid text for backend validation.
 * During editing, keep noncanonical spellings so trailing dots, separators and exponents can be completed.
 */
export function numericInput(
  text: string,
  kind: keyof typeof SYNTAX,
  editing = false,
): number | string {
  const trimmed = stripped(text);
  if (kind === "whole") {
    let digits = 0;
    // Python counts decimal digits, including leading zeroes, but not signs or underscores.
    for (const _digit of trimmed.matchAll(/\p{Decimal_Number}/gu))
      if (++digits > 4300) return editing ? text : trimmed;
  }
  const ascii = trimmed.replace(/\p{Decimal_Number}/gu, asciiDigit);
  if (!SYNTAX[kind].test(ascii)) return editing ? text : trimmed;
  const number = Number(ascii.replaceAll("_", ""));
  if (!Number.isFinite(number) || (kind === "whole" && !Number.isSafeInteger(number)))
    return editing ? text : trimmed;
  return editing && String(number) !== text ? text : number;
}
