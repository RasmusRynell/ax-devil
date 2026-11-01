import { CodePoints } from "./codePoints";
import type { AttrKindValue, SampleView, StringLookup } from "./sampleShape";

const NO_STRINGS: StringLookup = {
  count: 0,
  equals: () => false,
  decode: () => undefined,
  crc32: () => 0,
};

const encoder = new TextEncoder();

/** An attribute an expression reads; `kind` null means only its presence is tested. */
interface AttrSpec {
  readonly name: Uint8Array;
  readonly kind: AttrKindValue | null;
}

/**
 * The one object every compiled closure of a catalog or filter reads. `bind` re-points its typed arrays at the
 * current sample; nothing is allocated per row, and after the first large sample nothing per sample either.
 */
export class EvalContext {
  rows = 0;
  entityId: Int32Array = new Int32Array(0);
  box: Float32Array = new Float32Array(0);
  motion: Uint8Array = new Uint8Array(0);
  velocity: Float32Array = new Float32Array(0);
  classId: Int32Array = new Int32Array(0);
  score: Float64Array = new Float64Array(0);
  confidence: Float64Array = new Float64Array(0);
  maskWords = 0;
  classMask: Uint32Array = new Uint32Array(0);
  relKind: Int32Array = new Int32Array(0);
  relSource: Int32Array = new Int32Array(0);
  relTarget: Int32Array = new Int32Array(0);
  colorRgba: Uint32Array = new Uint32Array(0);
  colorScore: ArrayLike<number> = new Float32Array(0);
  strings: StringLookup = NO_STRINGS;
  /** Per literal: its id in this sample, or -3 - slot when the sample does not contain it. */
  lit: Int32Array = new Int32Array(0);
  /** Per attribute slot, the column of each kind (shared all-missing arrays when absent). */
  attrBool: Uint8Array[] = [];
  attrNumber: Float64Array[] = [];
  attrId: Int32Array[] = [];
  attrSpan: Int32Array[] = [];
  /** Values of variables: `stride` registers per entity row. */
  regs: Float64Array = new Float64Array(0);
  stride = 0;
  /** Which rules matched the current row. */
  ruleHit: Uint8Array = new Uint8Array(0);
  /** Text variables of the current sample. */
  textPool = new CodePoints();
  /** Attribute columns of another kind than the expressions need, in the last sample bound. */
  mismatched = 0;

  private readonly literals: Uint8Array[] = [];
  private readonly attrs: AttrSpec[] = [];
  private missingBool = new Uint8Array(0);
  private missingNumber = new Float64Array(0);
  private missingId = new Int32Array(0);
  private missingSpan = new Int32Array(0);

  /** Register a string literal; the same text shares one slot. */
  literal(text: string): number {
    const utf8 = encoder.encode(text);
    const found = this.literals.findIndex(
      (other) => other.length === utf8.length && other.every((byte, i) => byte === utf8[i]),
    );
    if (found >= 0) return found;
    this.literals.push(utf8);
    this.lit = new Int32Array(this.literals.length);
    return this.literals.length - 1;
  }

  /** Register an attribute read; the same name and kind share one slot. */
  attrSlot(name: string, kind: AttrKindValue | null): number {
    const utf8 = encoder.encode(name);
    const found = this.attrs.findIndex(
      (spec) =>
        spec.kind === kind &&
        spec.name.length === utf8.length &&
        spec.name.every((byte, i) => byte === utf8[i]),
    );
    if (found >= 0) return found;
    this.attrs.push({ name: utf8, kind });
    this.attrBool.push(this.missingBool);
    this.attrNumber.push(this.missingNumber);
    this.attrId.push(this.missingId);
    this.attrSpan.push(this.missingSpan);
    return this.attrs.length - 1;
  }

  /** Size the registers once the variables are laid out. */
  configure(stride: number, rules: number): void {
    this.stride = stride;
    this.ruleHit = new Uint8Array(rules);
  }

  /** Point at a sample and look its literals and attributes up. Allocates only when a sample outgrows the last. */
  bind(sample: SampleView): void {
    const rows = sample.rows;
    this.rows = rows;
    this.entityId = sample.entityId;
    this.box = sample.box;
    this.motion = sample.motion;
    this.velocity = sample.velocity;
    this.classId = sample.classId;
    this.score = sample.score;
    this.confidence = sample.confidence;
    this.maskWords = sample.maskWords;
    this.classMask = sample.classMask;
    this.relKind = sample.relKind;
    this.relSource = sample.relSource;
    this.relTarget = sample.relTarget;
    this.colorRgba = sample.colors.rgba;
    this.colorScore = sample.colors.score;
    this.strings = sample.strings;
    this.textPool.clear();
    this.growMissing(rows);
    if (this.regs.length < rows * this.stride)
      this.regs = new Float64Array(Math.max(rows * this.stride, this.regs.length * 2));
    this.bindLiterals(sample.strings);
    this.bindAttrs(sample);
  }

  private growMissing(rows: number): void {
    if (this.missingBool.length >= rows) return;
    const size = Math.max(rows, this.missingBool.length * 2);
    this.missingBool = new Uint8Array(size).fill(2);
    this.missingNumber = new Float64Array(size).fill(Number.NaN);
    this.missingId = new Int32Array(size).fill(-1);
    this.missingSpan = new Int32Array(size * 2).fill(-1);
  }

  private bindLiterals(strings: StringLookup): void {
    for (let k = 0; k < this.literals.length; k++) {
      const utf8 = this.literals[k];
      // A literal the sample lacks gets an id of its own, so two absent literals never compare equal.
      let found = -3 - k;
      for (let id = 0; id < strings.count; id++) {
        if (strings.equals(id, utf8)) {
          found = id;
          break;
        }
      }
      this.lit[k] = found;
    }
  }

  private bindAttrs(sample: SampleView): void {
    this.mismatched = 0;
    for (let slot = 0; slot < this.attrs.length; slot++) {
      const spec = this.attrs[slot];
      this.attrBool[slot] = this.missingBool;
      this.attrNumber[slot] = this.missingNumber;
      this.attrId[slot] = this.missingId;
      this.attrSpan[slot] = this.missingSpan;
      for (const column of sample.attrs) {
        if (!sample.strings.equals(column.name, spec.name)) continue;
        if (spec.kind !== null && spec.kind !== column.kind) {
          this.mismatched++;
          continue;
        }
        if (column.bool) this.attrBool[slot] = column.bool;
        if (column.number) this.attrNumber[slot] = column.number;
        if (column.id) this.attrId[slot] = column.id;
        if (column.span) this.attrSpan[slot] = column.span;
      }
    }
  }
}
