// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { TextAtlas } from "./atlas";
import { encodeSections, fakeGlyphs } from "./fixtures";
import { decodeInstructions } from "./instructions";
import { AtlasMirror } from "./mirror";
import { OverlayPreparer, type PreparedOverlay } from "./preparer";
import { QUAD_FLOATS } from "./quads";

// A 32 px page holds twelve 10 px glyphs (6 by 9 each, a pixel apart): four on each of three shelves.
const PAGE = 32;

function overlay(sequenceId: number, text: string, revision = 0) {
  const instructions = decodeInstructions(
    encodeSections({ paints: [{ fontSize: 0.1 }], texts: [{ x: 0.5, y: 0.5, text, paint: 0 }] }),
  );
  return {
    lane: 0,
    sequenceId,
    revision,
    instructions,
    target: { width: 100, height: 100 },
    dpr: 1,
    opacity: 1,
  };
}

const glyphs = ({ prepared }: PreparedOverlay) => prepared.texts.length / QUAD_FLOATS;

describe("TextAtlas and OverlayPreparer", () => {
  it("rasterizes each glyph once and uploads only the new ones", () => {
    const source = fakeGlyphs();
    const preparer = new OverlayPreparer(new TextAtlas(source, PAGE));
    const [first] = preparer.prepare(overlay(1, "abab"));
    const [second] = preparer.prepare(overlay(2, "ba"));
    expect(source.drawn).toEqual(["a", "b"]);
    expect(first.prepared.uploads).toEqual([
      { type: "region", x: 0, y: 0, width: 13, height: 9, alpha: expect.any(Uint8Array) },
    ]);
    expect([glyphs(first), glyphs(second), second.prepared.uploads.length]).toEqual([4, 2, 0]);
  });

  it("starts a new page when one fills up and prepares the held window again on it", () => {
    const source = fakeGlyphs();
    const atlas = new TextAtlas(source, PAGE);
    const preparer = new OverlayPreparer(atlas);
    const mirror = new AtlasMirror();
    const arrived = [
      ...preparer.prepare(overlay(1, "abcd")),
      ...preparer.prepare(overlay(2, "efgh")),
      ...preparer.prepare(overlay(3, "ijkl")),
    ];
    arrived.forEach(({ prepared }) => mirror.apply(prepared));
    expect([atlas.generation, mirror.generation]).toEqual([0, 0]);

    // Frame 1 left the window; frame 4 needs four more glyphs than the page has room for.
    preparer.retain(2, 4);
    const replaced = preparer.prepare(overlay(4, "mnop"));
    expect(replaced.map(({ sequenceId }) => sequenceId)).toEqual([4, 2, 3]);
    expect(replaced.map(({ prepared }) => prepared.generation)).toEqual([1, 1, 1]);
    expect(replaced.map(glyphs)).toEqual([4, 4, 4]);
    expect(replaced[0].prepared.uploads[0]).toEqual({ type: "reset", generation: 1 });
    expect(source.drawn).toEqual(["m", "n", "o", "p", "e", "f", "g", "h", "i", "j", "k", "l"]);

    replaced.forEach(({ prepared }) => mirror.apply(prepared));
    expect(mirror.generation).toBe(1);
    expect(mirror.takeDirty()).toEqual([0, 0, mirror.size, mirror.size]);
    expect([mirror.pixels[0], mirror.pixels[mirror.size * 30]]).toEqual([255, 0]);
    mirror.apply(preparer.prepare(overlay(5, "e"))[0].prepared);
    expect(mirror.takeDirty()).toBeNull();
    expect(preparer.size).toBe(4);
  });

  it("leaves out glyphs that do not fit even a fresh page instead of resetting again", () => {
    const atlas = new TextAtlas(fakeGlyphs(), PAGE);
    const preparer = new OverlayPreparer(atlas);
    const [only] = preparer.prepare(overlay(1, "abcdefghijklmnop"));
    expect([glyphs(only), atlas.dropped, only.prepared.generation]).toEqual([12, 4, 1]);
  });

  it("ignores an overlay older than the revision it holds", () => {
    const preparer = new OverlayPreparer(new TextAtlas(fakeGlyphs(), PAGE));
    preparer.prepare(overlay(1, "a", 2));
    expect(preparer.prepare(overlay(1, "b", 1))).toEqual([]);
    expect(preparer.prepare(overlay(1, "b", 2))).toHaveLength(1);
  });
});
