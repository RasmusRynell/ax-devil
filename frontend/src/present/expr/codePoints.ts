/** A growable run of code points; text expressions append to it and nothing is allocated per row. */
export class CodePoints {
  data = new Uint32Array(256);
  length = 0;

  /** Append one code point. */
  push(codePoint: number): void {
    if (this.length === this.data.length) this.reserve(1);
    this.data[this.length++] = codePoint;
  }

  /** Make room for `extra` more code points. */
  reserve(extra: number): void {
    if (this.length + extra <= this.data.length) return;
    const grown = new Uint32Array(Math.max(this.data.length * 2, this.length + extra));
    grown.set(this.data.subarray(0, this.length));
    this.data = grown;
  }

  /** Empty it, keeping the memory. */
  clear(): void {
    this.length = 0;
  }
}

const CHUNK = 4096;

/** Code points `start` to `end` of `data` as a string, built in bounded pieces so a long run cannot overflow the call stack. */
export function codePointString(data: Uint32Array, start: number, end: number): string {
  let text = "";
  for (let at = start; at < end; at += CHUNK)
    text += String.fromCodePoint(...data.subarray(at, Math.min(at + CHUNK, end)));
  return text;
}
