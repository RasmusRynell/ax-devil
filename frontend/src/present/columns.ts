/**
 * Reads a Sample Item: one Scene sample as typed columns, viewed over the received bytes without copying.
 *
 * `crates/scene/src/serial/columns.rs` and this file are the single source of truth for the layout: change both
 * together and bump the version. The encoder's module documentation describes every section, the string table, the
 * attribute columns and the class mask.
 *
 * Strings are stored once each in three regions: the motion states, then the vocabulary of classes and attributes,
 * then entity ids. A catalog literal therefore has one id whichever field it is compared with.
 *
 * Missing values: NaN in float columns, -1 in id columns (the encoder's `u32::MAX` read as `Int32Array`), 2 in a
 * boolean attribute. The item does not carry its source, data revision or sample index; the envelope around it does.
 */

import type { CodePoints } from "./expr/codePoints";

/** Why bytes are not a Sample Item this build reads. The item is dropped and the lane reports a problem. */
export class SampleFormatError extends Error {
  constructor(reason: string) {
    super(reason);
    this.name = "SampleFormatError";
  }
}

/** The values of the `motion` column. */
export const Motion = { None: 0, Moving: 1, Stationary: 2, Unknown: 3 } as const;

/** The first strings of every item, in `Motion` order after none: the string id of motion value `m` is `m - 1`. */
export const MOTION_STATES = ["moving", "stationary", "unknown"] as const;

const encoder = new TextEncoder();

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

/** The kinds of attribute column, as stored in the attribute index. */
export const AttrKind = { Bool: 0, Number: 1, String: 2, Colors: 3 } as const;
export type AttrKind = (typeof AttrKind)[keyof typeof AttrKind];

/** The cell of a boolean attribute column that has no value. */
export const BOOL_MISSING = 2;

/**
 * The strings of an item by id. Ids below `states` are the motion states, ids up to `vocab` the classes, attribute
 * names and values, colour names and relation kinds, the rest entity ids. Nothing here allocates except `string`.
 */
export class StringTable {
  readonly count: number;
  readonly states: number;
  readonly vocab: number;
  private readonly offsets: Uint32Array;
  private readonly data: Uint8Array;

  constructor(offsets: Uint32Array, data: Uint8Array, states: number, vocab: number) {
    this.count = offsets.length - 1;
    this.states = states;
    this.vocab = vocab;
    this.offsets = offsets;
    this.data = data;
  }

  /** Whether string `id` is exactly the UTF-8 `bytes`. */
  equals(id: number, bytes: Uint8Array): boolean {
    this.check(id);
    const start = this.offsets[id];
    if (this.offsets[id + 1] - start !== bytes.length) return false;
    for (let i = 0; i < bytes.length; i++) if (this.data[start + i] !== bytes[i]) return false;
    return true;
  }

  /** Append the code points of string `id` to `out`. Malformed UTF-8 becomes U+FFFD. */
  decode(id: number, out: CodePoints): void {
    this.check(id);
    const { data } = this;
    const end = this.offsets[id + 1];
    let i = this.offsets[id];
    while (i < end) {
      const lead = data[i++];
      let extra = 0;
      let point = lead;
      if (lead >= 0xf0) {
        extra = 3;
        point = lead & 0x07;
      } else if (lead >= 0xe0) {
        extra = 2;
        point = lead & 0x0f;
      } else if (lead >= 0xc0) {
        extra = 1;
        point = lead & 0x1f;
      } else if (lead >= 0x80) {
        point = 0xfffd;
      }
      for (; extra > 0; extra--) {
        const next = i < end ? data[i] : 0;
        if ((next & 0xc0) !== 0x80) {
          point = 0xfffd;
          break;
        }
        point = (point << 6) | (next & 0x3f);
        i++;
      }
      out.push(point);
    }
  }

  /** The CRC-32 (IEEE, as `crc32fast`) of the UTF-8 bytes of string `id`, unsigned. */
  crc32(id: number): number {
    this.check(id);
    let crc = 0xffffffff;
    for (let i = this.offsets[id], end = this.offsets[id + 1]; i < end; i++)
      crc = CRC_TABLE[(crc ^ this.data[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  /** String `id` as text. Allocates: for the inspector and tests, not the per-frame path. */
  string(id: number): string {
    this.check(id);
    return new TextDecoder().decode(this.data.subarray(this.offsets[id], this.offsets[id + 1]));
  }

  private check(id: number): void {
    if (!(id >= 0 && id < this.count))
      throw new SampleFormatError(`string ${id} is not in the item`);
  }
}

/** One attribute of the primary classification for one kind: a cell per row, in the field the kind names. */
export interface AttrColumn {
  /** String id of the attribute name. */
  readonly name: number;
  readonly kind: AttrKind;
  /** 0 or 1, `BOOL_MISSING` when the row has no value. */
  readonly bool?: Uint8Array;
  /** NaN when the row has no value. */
  readonly number?: Float64Array;
  /** String id, -1 when the row has no value. */
  readonly id?: Int32Array;
  /** First colour and colour count per row; first is -1 when the row has no value, an empty list has count 0. */
  readonly span?: Int32Array;
}

/** The columns of one sample. Row `i` is the `i`-th entity of the Scene that has an observation. */
export interface SampleView {
  readonly rows: number;
  readonly relationCount: number;
  readonly entityId: Int32Array;
  /** Left, top, right, bottom per row, as the Scene stores them (normalised to the frame); a polygon's bounds. */
  readonly box: Float32Array;
  /** First point and point count per row into `polyPoints`; a count of 0 is a box. */
  readonly polyIndex: Uint32Array;
  /** x, y pairs. */
  readonly polyPoints: Float32Array;
  /** `Motion` values. */
  readonly motion: Uint8Array;
  /** vx, vy per row; NaN when unknown. */
  readonly velocity: Float32Array;
  /** String id of the primary classification's type, -1 for none. */
  readonly classId: Int32Array;
  readonly score: Float64Array;
  readonly confidence: Float64Array;
  /** Words per row of `classMask`: one per 32 filter options of the lane's policy. */
  readonly maskWords: number;
  /** `maskWords` words per row; bit `i % 32` of word `i / 32` is set when filter option `i` selects the entity. */
  readonly classMask: Uint32Array;
  readonly attrs: readonly AttrColumn[];
  readonly colors: {
    /** 0xRRGGBBAA, alpha 255. */
    readonly rgba: Uint32Array;
    /** NaN when the colour has no score. */
    readonly score: Float64Array;
    /** String id, -1 when the colour has no name. */
    readonly name: Int32Array;
  };
  readonly relKind: Int32Array;
  /** Row of the relation's source, -1 when the entity is not a row. */
  readonly relSource: Int32Array;
  readonly relTarget: Int32Array;
  readonly strings: StringTable;
}

const MAGIC = 0x4953_5841; // "AXSI"
const VERSION = 1;
const HEADER_WORDS = 12;
const SECTION_COUNT = 19;
const HEADER_BYTES = (HEADER_WORDS + SECTION_COUNT) * 4;

/** Positions in the header's offset table, in the encoder's `Section` order. */
const S = {
  entityId: 0,
  box: 1,
  polyIndex: 2,
  polyPoints: 3,
  motion: 4,
  velocity: 5,
  class: 6,
  score: 7,
  confidence: 8,
  classMask: 9,
  attrIndex: 10,
  colorRgba: 11,
  colorScore: 12,
  colorName: 13,
  relKind: 14,
  relSource: 15,
  relTarget: 16,
  stringOffsets: 17,
  stringBytes: 18,
} as const;

/** Bytes per cell of an attribute column of each kind, indexed by `AttrKind`. */
const ATTR_CELL_BYTES = [1, 8, 4, 8] as const;

/**
 * The columns of a Sample Item, as views of `buffer`. An item must start at a multiple of 8 to be viewed in place;
 * an unaligned `Uint8Array` is copied once.
 */
export function readSample(buffer: ArrayBuffer | Uint8Array): SampleView {
  let bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.byteOffset % 8 !== 0) bytes = bytes.slice();
  const { buffer: data, byteOffset: base, byteLength: length } = bytes;
  if (length < HEADER_BYTES) throw new SampleFormatError("the item is shorter than its header");
  const header = new DataView(data, base, HEADER_BYTES);
  const word = (index: number): number => header.getUint32(index * 4, true);
  if (word(0) !== MAGIC) throw new SampleFormatError("the bytes are not a sample item");
  if (word(1) !== VERSION)
    throw new SampleFormatError(`sample item version ${word(1)} is not read here`);
  if (length % 8 !== 0) throw new SampleFormatError("the item's length is not a multiple of 8");
  const [
    rows,
    relations,
    points,
    colors,
    attributes,
    maskWords,
    strings,
    states,
    vocab,
    stringBytes,
  ] = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(word);

  /** The absolute byte offset of `size` bytes at `offset`, which must lie in the item and start on a multiple of 8. */
  const place = (offset: number, size: number): number => {
    if (offset % 8 !== 0 || offset < HEADER_BYTES || offset + size > length)
      throw new SampleFormatError("a section lies outside the item");
    return base + offset;
  };
  const at = (section: number, size: number): number => place(word(HEADER_WORDS + section), size);

  const stringOffsets = new Uint32Array(data, at(S.stringOffsets, (strings + 1) * 4), strings + 1);
  const stringData = new Uint8Array(data, at(S.stringBytes, stringBytes), stringBytes);
  if (
    stringOffsets[0] !== 0 ||
    stringOffsets[strings] !== stringBytes ||
    states !== MOTION_STATES.length ||
    states > vocab ||
    vocab > strings
  )
    throw new SampleFormatError("the string table is inconsistent");
  const table = new StringTable(stringOffsets, stringData, states, vocab);
  MOTION_STATES.forEach((name, id) => {
    if (!table.equals(id, encoder.encode(name)))
      throw new SampleFormatError("the string table does not start with the motion states");
  });

  const index = new Uint32Array(data, at(S.attrIndex, attributes * 12), attributes * 3);
  const attrs: AttrColumn[] = [];
  for (let i = 0; i < attributes; i++) {
    const [name, kind, offset] = [index[i * 3], index[i * 3 + 1], index[i * 3 + 2]];
    if (kind > AttrKind.Colors)
      throw new SampleFormatError(`attribute kind ${kind} is not read here`);
    const cells = place(offset, rows * ATTR_CELL_BYTES[kind]);
    attrs.push({
      name,
      kind: kind as AttrKind,
      bool: kind === AttrKind.Bool ? new Uint8Array(data, cells, rows) : undefined,
      number: kind === AttrKind.Number ? new Float64Array(data, cells, rows) : undefined,
      id: kind === AttrKind.String ? new Int32Array(data, cells, rows) : undefined,
      span: kind === AttrKind.Colors ? new Int32Array(data, cells, rows * 2) : undefined,
    });
  }

  return {
    rows,
    relationCount: relations,
    entityId: new Int32Array(data, at(S.entityId, rows * 4), rows),
    box: new Float32Array(data, at(S.box, rows * 16), rows * 4),
    polyIndex: new Uint32Array(data, at(S.polyIndex, rows * 8), rows * 2),
    polyPoints: new Float32Array(data, at(S.polyPoints, points * 8), points * 2),
    motion: new Uint8Array(data, at(S.motion, rows), rows),
    velocity: new Float32Array(data, at(S.velocity, rows * 8), rows * 2),
    classId: new Int32Array(data, at(S.class, rows * 4), rows),
    score: new Float64Array(data, at(S.score, rows * 8), rows),
    confidence: new Float64Array(data, at(S.confidence, rows * 8), rows),
    maskWords,
    classMask: new Uint32Array(data, at(S.classMask, rows * maskWords * 4), rows * maskWords),
    attrs,
    colors: {
      rgba: new Uint32Array(data, at(S.colorRgba, colors * 4), colors),
      score: new Float64Array(data, at(S.colorScore, colors * 8), colors),
      name: new Int32Array(data, at(S.colorName, colors * 4), colors),
    },
    relKind: new Int32Array(data, at(S.relKind, relations * 4), relations),
    relSource: new Int32Array(data, at(S.relSource, relations * 4), relations),
    relTarget: new Int32Array(data, at(S.relTarget, relations * 4), relations),
    strings: table,
  };
}
