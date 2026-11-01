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
