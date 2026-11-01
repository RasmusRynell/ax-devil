/**
 * The Sample Items a viewer holds, per source: parsed on arrival, evicted to the samples the Frame Maps it holds select
 * inside its data window, and never beyond a byte cap.
 */

import { SampleFormatError, readSample, type SampleView } from "../../present";

type Source = {
  revision: number;
  items: Map<number, { view: SampleView; bytes: number }>;
  bytes: number;
};

export class SampleStore {
  readonly #sources = new Map<number, Source>();
  readonly #cap: number;
  #bytes = 0;

  /** `capBytes` is the most the items of all sources may take after `retain`. */
  constructor(capBytes: number) {
    this.#cap = capBytes;
  }

  /** Bytes of the items held. */
  get bytes(): number {
    return this.#bytes;
  }

  /** The revision a source's items must carry; another revision drops everything held for the source. */
  setRevision(source: number, dataRevision: number): void {
    const held = this.#sources.get(source);
    if (held?.revision === dataRevision) return;
    if (held) this.#drop(held);
    this.#sources.set(source, { revision: dataRevision, items: new Map(), bytes: 0 });
  }

  /** Parses and keeps an item of the source's current revision. An item that cannot be read is not kept; its reason is returned. */
  add(source: number, dataRevision: number, sample: number, item: Uint8Array): string | null {
    const held = this.#sources.get(source);
    if (held?.revision !== dataRevision) return null;
    let view: SampleView;
    try {
      view = readSample(item);
    } catch (error) {
      if (error instanceof SampleFormatError) return error.message;
      throw error;
    }
    const before = held.items.get(sample)?.bytes ?? 0;
    held.items.set(sample, { view, bytes: item.byteLength });
    held.bytes += item.byteLength - before;
    this.#bytes += item.byteLength - before;
    return null;
  }

  get(source: number, sample: number): SampleView | undefined {
    return this.#sources.get(source)?.items.get(sample)?.view;
  }

  /**
   * Keeps the held items among `wanted`, most urgent first, that fit the cap with the other sources' items (the most
   * urgent stays even when it alone is over), and drops the rest. A dropped sample a map still names is simply not drawn until the backend sends it again.
   */
  retain(source: number, wanted: readonly number[]): void {
    const held = this.#sources.get(source);
    if (!held) return;
    const room = Math.max(0, this.#cap - (this.#bytes - held.bytes));
    const kept = new Map<number, { view: SampleView; bytes: number }>();
    let bytes = 0;
    for (const sample of wanted) {
      const entry = held.items.get(sample);
      // The most urgent item stays whatever it weighs: the cap bounds the rest.
      if (!entry || (kept.size > 0 && bytes + entry.bytes > room)) continue;
      kept.set(sample, entry);
      bytes += entry.bytes;
    }
    this.#bytes += bytes - held.bytes;
    held.items = kept;
    held.bytes = bytes;
  }

  clear(): void {
    this.#sources.clear();
    this.#bytes = 0;
  }

  #drop(source: Source): void {
    this.#bytes -= source.bytes;
  }
}
