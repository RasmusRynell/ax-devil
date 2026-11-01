/** The Sample Items a viewer holds, per source: parsed on arrival, evicted by the window the Frame Maps define. */

import { SampleFormatError, readSample, type SampleView } from "../../present";

type Source = {
  revision: number;
  items: Map<number, { view: SampleView; bytes: number }>;
  /** The samples the maps of the window select, once the viewer holds all of them; `null` before. */
  window: ReadonlySet<number> | null;
};

export class SampleStore {
  readonly #sources = new Map<number, Source>();
  #bytes = 0;

  /** Bytes of the items held. */
  get bytes(): number {
    return this.#bytes;
  }

  /** The revision a source's items must carry; another revision drops everything held for the source. */
  setRevision(source: number, dataRevision: number): void {
    const held = this.#sources.get(source);
    if (held?.revision === dataRevision) return;
    if (held) this.#drop(held);
    this.#sources.set(source, { revision: dataRevision, items: new Map(), window: null });
  }

  /**
   * Parses and keeps an item of the source's current revision, unless the source's window is known and lacks the
   * sample. An item that cannot be read is not kept; its reason is returned.
   */
  add(source: number, dataRevision: number, sample: number, item: Uint8Array): string | null {
    const held = this.#sources.get(source);
    if (held?.revision !== dataRevision || (held.window && !held.window.has(sample))) return null;
    let view: SampleView;
    try {
      view = readSample(item);
    } catch (error) {
      if (error instanceof SampleFormatError) return error.message;
      throw error;
    }
    const before = held.items.get(sample);
    if (before) this.#bytes -= before.bytes;
    held.items.set(sample, { view, bytes: item.byteLength });
    this.#bytes += item.byteLength;
    return null;
  }

  get(source: number, sample: number): SampleView | undefined {
    return this.#sources.get(source)?.items.get(sample)?.view;
  }

  /** Keeps exactly `window` for the source; `null` (the maps are incomplete) keeps everything held. */
  retain(source: number, window: ReadonlySet<number> | null): void {
    const held = this.#sources.get(source);
    if (!held) return;
    held.window = window;
    if (!window) return;
    for (const [sample, { bytes }] of held.items) {
      if (window.has(sample)) continue;
      this.#bytes -= bytes;
      held.items.delete(sample);
    }
  }

  clear(): void {
    this.#sources.clear();
    this.#bytes = 0;
  }

  #drop(source: Source): void {
    for (const { bytes } of source.items.values()) this.#bytes -= bytes;
    source.items.clear();
  }
}
