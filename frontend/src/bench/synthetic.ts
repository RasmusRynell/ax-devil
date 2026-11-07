/** Inputs made up for the bench: I420 frames, catalogs compiled as the app does, and rings of Sample Items. */

import classic from "../../../assets/catalogs/classic.v4.json?raw";
import glass from "../../../assets/catalogs/glass.v4.json?raw";
import standard from "../../../assets/catalogs/standard.v4.json?raw";
import type { LaneFrame } from "../playback/contract";
import type { VideoFrame } from "../playback/protocol";
import { type CompiledCatalog } from "../present";
import { compileCatalog } from "../present/catalog/compile";
import { syntheticSample } from "../present/fixtures";
import type { Resolution } from "./types";

const SOURCES: Record<string, string> = { standard, classic, glass };

export const SIZES: Record<Resolution, { width: number; height: number }> = {
  small: { width: 640, height: 360 },
  "1080p": { width: 1920, height: 1080 },
  "4k": { width: 3840, height: 2160 },
};

/** Catalog `name` compiled from the built-in file; throws with the diagnostics when it does not compile. */
export function catalogNamed(name: string): CompiledCatalog {
  const source = SOURCES[name];
  if (source === undefined) throw new Error(`no built-in catalog ${name}`);
  const result = compileCatalog(source);
  if (!result.ok)
    throw new Error(
      `${name}: ${result.diagnostics.map((d) => `${d.pointer} ${d.message}`).join("; ")}`,
    );
  return result.catalog;
}

/** A tightly packed I420 frame whose planes differ with `variant`, so each upload moves real bytes. */
export function videoFrame(width: number, height: number, variant: number): VideoFrame {
  const chromaWidth = Math.ceil(width / 2);
  const chromaHeight = Math.ceil(height / 2);
  const plane = (w: number, h: number, step: number): Uint8Array => {
    const bytes = new Uint8Array(w * h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) bytes[y * w + x] = (x * step + y * 3 + variant * 41) & 255;
    return bytes;
  };
  const planes: VideoFrame["planes"] = [
    plane(width, height, 7),
    plane(chromaWidth, chromaHeight, 5),
    plane(chromaWidth, chromaHeight, 3),
  ];
  return {
    sequenceId: variant,
    timestampUs: variant * 33_333,
    keyframe: variant === 0,
    last: false,
    width,
    height,
    format: "i420",
    matrix: "bt709",
    fullRange: false,
    strides: [width, chromaWidth, chromaWidth],
    planes,
    byteLength: planes.reduce((sum, bytes) => sum + bytes.length, 0),
  };
}

/**
 * `length` steps of a play: step `k` holds one lane frame per lane, each over a sample of its own that no other step
 * or lane shares, so every step is new data to every lane.
 */
export function laneRing(
  catalog: CompiledCatalog,
  lanes: number,
  objects: number,
  length: number,
): LaneFrame[][] {
  return Array.from({ length }, (_, k) =>
    Array.from({ length: lanes }, (_, lane) => {
      const columns = syntheticSample(objects, lane * length + k);
      return {
        source: lane,
        dataRevision: 0,
        sample: k,
        columns,
        catalog,
        styleRevision: 0,
        classMask: { enabled: new Uint32Array(columns.maskWords), options: 0 },
        filter: null,
        hidden: 0,
        theme: null,
        opacity: 1,
      };
    }),
  );
}
