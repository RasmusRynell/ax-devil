/**
 * The Sample Items a viewer holds, per source: parsed on arrival and dropped only when the backend evicts them, the
 * source's data revision changes or the source fails. The backend keeps the bytes within the limit the viewer opened
 * the stream with; the store reports an item that breaks it.
 */

import { SampleFormatError, readSample, type SampleView } from "../../present";

/** When the sample is on the video clock and which frame of its file it is; `null` where the backend has none. */
export type SampleKey = { timeUs: number | null; frame: number | null };

const NO_KEY: SampleKey = { timeUs: null, frame: null };

type Source = {
  revision: number;
  items: Map<number, { view: SampleView; bytes: number; key: SampleKey }>;
  bytes: number;
};

export class SampleStore {
  readonly #sources = new Map<number, Source>();
  readonly #limit: number;
  #bytes = 0;
  #count = 0;

  /** `limitBytes` is the most the items of all sources may take, except one item that is larger alone. */
  constructor(limitBytes: number) {
    this.#limit = limitBytes;
  }

  /** Bytes of the items held. */
  get bytes(): number {
    return this.#bytes;
  }

  /** Number of items held, over all sources. */
  get count(): number {
    return this.#count;
  }

  /** The revision a source's items must carry; another revision drops everything held for the source. */
  setRevision(source: number, dataRevision: number): void {
    const held = this.#sources.get(source);
    if (held?.revision === dataRevision) return;
    if (held) this.#drop(held);
    this.#sources.set(source, { revision: dataRevision, items: new Map(), bytes: 0 });
  }

  /**
   * Parses and keeps an item of the source's current revision; an item of another revision is ignored. Returns why the
   * item cannot be accepted, `null` otherwise: it cannot be read (and is not kept), or the items held outgrew the
   * limit with more than one of them.
   */
  add(
    source: number,
    dataRevision: number,
    sample: number,
    item: Uint8Array,
    key: SampleKey = NO_KEY,
  ): string | null {
    const held = this.#sources.get(source);
    if (held?.revision !== dataRevision) return null;
    let view: SampleView;
    try {
      view = readSample(item);
    } catch (error) {
      if (error instanceof SampleFormatError) return error.message;
      throw error;
    }
    const before = held.items.get(sample)?.bytes;
    held.items.set(sample, { view, bytes: item.byteLength, key });
    held.bytes += item.byteLength - (before ?? 0);
    this.#bytes += item.byteLength - (before ?? 0);
    if (before === undefined) this.#count++;
    if (this.#bytes > this.#limit && this.#count > 1)
      return `the sample items held take ${this.#bytes} bytes, over the ${this.#limit} the viewer allowed`;
    return null;
  }

  /** Drops the source's items `samples`; ignored for another revision, and for an item not held. */
  evict(source: number, dataRevision: number, samples: readonly number[]): void {
    const held = this.#sources.get(source);
    if (held?.revision !== dataRevision) return;
    for (const sample of samples) {
      const entry = held.items.get(sample);
      if (!entry) continue;
      held.items.delete(sample);
      held.bytes -= entry.bytes;
      this.#bytes -= entry.bytes;
      this.#count--;
    }
  }

  get(source: number, sample: number): SampleView | undefined {
    return this.#sources.get(source)?.items.get(sample)?.view;
  }

  /** When the held sample is on the video clock and its frame in its file; `undefined` for a sample not held. */
  keyOf(source: number, sample: number): SampleKey | undefined {
    return this.#sources.get(source)?.items.get(sample)?.key;
  }

  clear(): void {
    this.#sources.clear();
    this.#bytes = 0;
    this.#count = 0;
  }

  #drop(source: Source): void {
    this.#bytes -= source.bytes;
    this.#count -= source.items.size;
  }
}
