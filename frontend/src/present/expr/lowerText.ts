import type { CodePoints } from "./codePoints";
import type { Emitter, RowFn } from "./compiled";
import type { EvalContext } from "./context";
import { emitFixed } from "./fixed";

/** Text that is always the same. */
export function constantText(text: string): Emitter {
  const points = Array.from(text, (char) => char.codePointAt(0) ?? 0);
  return (_row, out) => {
    for (const point of points) out.push(point);
    return true;
  };
}

/** The text of a string id; null for no string. */
export function stringText(ctx: EvalContext, id: RowFn): Emitter {
  return (row, out) => {
    const value = id(row);
    if (value < 0) return false;
    ctx.strings.decode(value, out);
    return true;
  };
}

/** Text that is never there. */
export const noText: Emitter = () => false;

/** The first of two texts that is not null. */
export function coalesceText(first: Emitter, second: Emitter): Emitter {
  return (row, out) => {
    const mark = out.length;
    if (first(row, out)) return true;
    out.length = mark;
    return second(row, out);
  };
}

/** One of two texts by a condition. */
export function chooseText(condition: RowFn, then: Emitter, otherwise: Emitter): Emitter {
  return (row, out) => (condition(row) === 1 ? then(row, out) : otherwise(row, out));
}

/** The text of the first key equal to the subject, else the default's. */
export function selectText(
  subject: RowFn,
  keys: readonly RowFn[],
  results: readonly Emitter[],
  fallback: Emitter,
): Emitter {
  return (row, out) => {
    const value = subject(row);
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i](row);
      if (value === key || (value !== value && key !== key)) return results[i](row, out);
    }
    return fallback(row, out);
  };
}

/** The code points a variable stored: start and length in two registers, NaN start for null. */
export function storedText(ctx: EvalContext, register: number): Emitter {
  return (row, out) => {
    const base = row * ctx.stride + register;
    const start = ctx.regs[base];
    if (start !== start) return false;
    const end = start + ctx.regs[base + 1];
    for (let i = start; i < end; i++) out.push(ctx.textPool.data[i]);
    return true;
  };
}

/** A number with fixed decimals, rounded half to even; null for no number. */
export function fixedText(value: RowFn, places: number): Emitter {
  return (row, out) => emitFixed(value(row), places, out);
}

function find(data: Uint32Array, from: number, to: number, needle: Uint32Array): number {
  for (let i = from; i + needle.length <= to; i++) {
    let j = 0;
    while (j < needle.length && data[i + j] === needle[j]) j++;
    if (j === needle.length) return i;
  }
  return -1;
}

const points = (text: string): Uint32Array =>
  Uint32Array.from(Array.from(text, (char) => char.codePointAt(0) ?? 0));

/**
 * Shorten a text to `count` code points. At the end it is cut at the first delimiter when one is given (also text that
 * fits) and marked with the suffix when that changed it; in the middle it keeps both ends with the suffix between, so
 * ids that differ only at their start or end stay apart. A null or negative count makes the text null.
 */
export function trimText(
  inner: Emitter,
  count: RowFn,
  where: string,
  suffixText: string,
  delimiterText: string,
): Emitter {
  const suffix = points(suffixText);
  const delimiter = points(delimiterText);
  const middle = where === "middle";
  return (row, out: CodePoints) => {
    const start = out.length;
    if (!inner(row, out)) return false;
    const wanted = count(row);
    if (!(wanted >= 0)) {
      out.length = start;
      return false;
    }
    const length = out.length - start;
    const limit = Math.floor(wanted);
    if (middle) {
      if (length <= limit) return true;
      const head = Math.ceil(limit / 2);
      const tail = limit - head;
      out.reserve(suffix.length);
      out.data.copyWithin(start + head + suffix.length, out.length - tail, out.length);
      out.data.set(suffix, start + head);
      out.length = start + head + suffix.length + tail;
      return true;
    }
    let keep = Math.min(limit, length);
    if (delimiter.length > 0) {
      const cut = find(out.data, start, start + keep, delimiter);
      if (cut >= 0) keep = cut - start;
    }
    if (keep === length) return true;
    out.length = start + keep;
    for (const point of suffix) out.push(point);
    return true;
  };
}
