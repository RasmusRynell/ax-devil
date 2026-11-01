/**
 * The glyph atlas as presentation sees it. Presentation asks for code points; the atlas rasterizes strings, so only
 * `whole` converts. The atlas's page changes are applied to the GPU by the painter through `atlas.takeUploads()`.
 */

import type { CodePoints, FontHandle, Glyph, TextSource } from "../present";
import type { AtlasFont, TextAtlas } from "./atlas";

export class AtlasText implements TextSource {
  constructor(readonly atlas: TextAtlas) {}

  get generation(): number {
    return this.atlas.generation;
  }

  get dropOverflow(): boolean {
    return this.atlas.dropOverflow;
  }

  set dropOverflow(drop: boolean) {
    this.atlas.dropOverflow = drop;
  }

  font(family: string, devicePixels: number, weight: number): FontHandle {
    return this.atlas.font(family, devicePixels, weight);
  }

  // The handles are the atlas's own fonts, which only `font` hands out.
  glyph(font: FontHandle, codePoint: number): Glyph {
    return this.atlas.glyph(font as AtlasFont, codePoint);
  }

  whole(font: FontHandle, text: CodePoints, start: number, end: number): Glyph {
    const line = String.fromCodePoint(...text.data.subarray(start, end));
    return this.atlas.whole(font as AtlasFont, line);
  }

  reset(): void {
    this.atlas.reset();
  }
}
