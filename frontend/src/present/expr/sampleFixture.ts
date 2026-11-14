import { MOTION_STATES, StringTable } from "../columns";
import { AttrKind, type AttrColumnView, type SampleView } from "./sampleShape";

/** A color with its score, as the wire's color lists hold them. */
export interface FixtureColor {
  readonly rgba: number;
  readonly score: number;
}

/** One entity of a hand-written sample. */
export interface FixtureEntity {
  readonly id: string;
  readonly class?: string;
  readonly score?: number;
  readonly confidence?: number;
  readonly motion?: "moving" | "stationary" | "unknown";
  readonly velocity?: readonly [number, number];
  /** The world velocity's speed. */
  readonly speed?: number;
  readonly box?: readonly [number, number, number, number];
  /** Class mask words of the row; every row has as many words as the longest. */
  readonly mask?: readonly number[];
  readonly attrs?: Readonly<Record<string, boolean | number | string | readonly FixtureColor[]>>;
}

/** One relation of a hand-written sample; ends are entity indexes. */
export interface FixtureRelation {
  readonly kind: string;
  readonly source: number;
  readonly target: number;
}

const encoder = new TextEncoder();

/** Strings as the encoder lays them out: motion states first, then the vocabulary, then ids; each stored once. */
class Strings {
  private readonly texts: string[] = [];
  private readonly index = new Map<string, number>();
  vocab = 0;

  constructor() {
    for (const state of MOTION_STATES) this.add(state);
  }

  get count(): number {
    return this.texts.length;
  }

  add(text: string): number {
    const found = this.index.get(text);
    if (found !== undefined) return found;
    this.texts.push(text);
    this.index.set(text, this.texts.length - 1);
    return this.texts.length - 1;
  }

  table(): StringTable {
    const encoded = this.texts.map((text) => encoder.encode(text));
    const offsets = new Uint32Array(encoded.length + 1);
    encoded.forEach((bytes, i) => (offsets[i + 1] = offsets[i] + bytes.length));
    const data = new Uint8Array(offsets[encoded.length]);
    encoded.forEach((bytes, i) => data.set(bytes, offsets[i]));
    return new StringTable(offsets, data, MOTION_STATES.length, this.vocab);
  }
}

/** Build the columns of a sample from a description. */
export function makeSample(
  entities: readonly FixtureEntity[],
  relations: readonly FixtureRelation[] = [],
  vocabulary: readonly string[] = [],
): SampleView {
  const strings = new Strings();
  for (const word of vocabulary) strings.add(word);
  for (const entity of entities) {
    if (entity.class !== undefined) strings.add(entity.class);
    for (const [name, value] of Object.entries(entity.attrs ?? {})) {
      strings.add(name);
      if (typeof value === "string") strings.add(value);
    }
  }
  for (const relation of relations) strings.add(relation.kind);
  strings.vocab = strings.count;
  const rows = entities.length;
  const maskWords = Math.max(0, ...entities.map((entity) => entity.mask?.length ?? 0));
  const colors: FixtureColor[] = [];
  const columns = new Map<
    string,
    {
      name: number;
      kind: number;
      bool: Uint8Array;
      number: Float64Array;
      id: Int32Array;
      span: Int32Array;
    }
  >();
  const column = (
    name: string,
    kind: number,
  ): AttrColumnView & {
    bool: Uint8Array;
    number: Float64Array;
    id: Int32Array;
    span: Int32Array;
  } => {
    const key = `${name}:${kind}`;
    let found = columns.get(key);
    if (!found) {
      found = {
        name: strings.add(name),
        kind,
        bool: new Uint8Array(rows).fill(2),
        number: new Float64Array(rows).fill(Number.NaN),
        id: new Int32Array(rows).fill(-1),
        span: new Int32Array(rows * 2).fill(-1),
      };
      columns.set(key, found);
    }
    return found as ReturnType<typeof column>;
  };
  const sample = {
    rows,
    relationCount: relations.length,
    entityId: new Int32Array(rows),
    box: new Float32Array(rows * 4),
    motion: new Uint8Array(rows),
    velocity: new Float32Array(rows * 2).fill(Number.NaN),
    speed: new Float64Array(rows).fill(Number.NaN),
    classId: new Int32Array(rows).fill(-1),
    score: new Float64Array(rows).fill(Number.NaN),
    confidence: new Float64Array(rows).fill(Number.NaN),
    maskWords,
    classMask: new Uint32Array(rows * maskWords),
    relKind: new Int32Array(relations.length),
    relSource: new Int32Array(relations.length),
    relTarget: new Int32Array(relations.length),
  };
  entities.forEach((entity, row) => {
    sample.entityId[row] = strings.add(entity.id);
    sample.box.set(entity.box ?? [0.1, 0.1, 0.3, 0.4], row * 4);
    sample.motion[row] = entity.motion
      ? ["moving", "stationary", "unknown"].indexOf(entity.motion) + 1
      : 0;
    if (entity.velocity) sample.velocity.set(entity.velocity, row * 2);
    sample.speed[row] = entity.speed ?? Number.NaN;
    if (entity.class !== undefined) sample.classId[row] = strings.add(entity.class);
    sample.score[row] = entity.score ?? Number.NaN;
    sample.confidence[row] = entity.confidence ?? Number.NaN;
    sample.classMask.set(entity.mask ?? [], row * maskWords);
    for (const [name, value] of Object.entries(entity.attrs ?? {})) {
      if (typeof value === "boolean") column(name, AttrKind.Bool).bool[row] = value ? 1 : 0;
      else if (typeof value === "number") column(name, AttrKind.Number).number[row] = value;
      else if (typeof value === "string")
        column(name, AttrKind.String).id[row] = strings.add(value);
      else {
        const list = column(name, AttrKind.Colors);
        list.span[row * 2] = colors.length;
        list.span[row * 2 + 1] = value.length;
        colors.push(...value);
      }
    }
  });
  relations.forEach((relation, i) => {
    sample.relKind[i] = strings.add(relation.kind);
    sample.relSource[i] = relation.source;
    sample.relTarget[i] = relation.target;
  });
  return {
    ...sample,
    attrs: [...columns.values()] as AttrColumnView[],
    colors: {
      rgba: Uint32Array.from(colors, (c) => c.rgba),
      score: Float32Array.from(colors, (c) => c.score),
    },
    strings: strings.table(),
  };
}
