// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vite-plus/test";
import { Motion, SampleFormatError, readSample, type SampleView } from "./columns";

// The goldens are the encoder's real bytes (crates/scene/src/serial/columns/tests.rs writes them); the Scenes they
// were encoded from are the oracle sheets and the MOT scenes next to them.
const ORACLE = new URL("../../../fixtures/oracle/", import.meta.url);

function bytes(path: string): ArrayBuffer {
  return new Uint8Array(readFileSync(new URL(path, ORACLE))).buffer;
}

function scenes(path: string): Scene[] {
  return (JSON.parse(readFileSync(new URL(path, ORACLE), "utf8")) as { scenes: Scene[] }).scenes;
}

interface Scene {
  entities: {
    id: string;
    observations: {
      geometry: { box?: number[]; polygon?: number[][] };
      classifications: { type: string; score: number | null; confidence: number | null }[];
      measurements?: { image_velocity?: { vx: number; vy: number } };
    }[];
    motion_state: string | null;
  }[];
  relations: { type: string; source: string; target: string }[];
}

const MOTION: Record<string, number> = {
  moving: Motion.Moving,
  stationary: Motion.Stationary,
  unknown: Motion.Unknown,
};

/** Every golden with the Scene it was encoded from. */
function goldens(): { name: string; scene: Scene }[] {
  const items = new Set(readdirSync(new URL("items/", ORACLE)).map((f) => f.replace(".bin", "")));
  const found: { name: string; scene: Scene }[] = [];
  for (const name of items) {
    const mot = /^mot_(det|gt)_(\d+)$/.exec(name);
    const scene = mot
      ? scenes(`mot/${mot[1]}.scenes.json`)[Number(mot[2])]
      : scenes(`sheets/${name}.json`)[0];
    found.push({ name, scene });
  }
  return found;
}

function text(view: SampleView, id: number): string | null {
  return id < 0 ? null : view.strings.string(id);
}

describe("readSample", () => {
  it("reads every golden as the oracle Scene it was encoded from", () => {
    const all = goldens();
    expect(all.length).toBeGreaterThan(10);
    for (const { name, scene } of all) {
      const view = readSample(bytes(`items/${name}.bin`));
      const entities = scene.entities.filter((e) => e.observations.length > 0);
      expect(view.rows, name).toBe(entities.length);
      entities.forEach((entity, row) => {
        const latest = entity.observations.at(-1)!;
        const where = `${name} ${entity.id}`;
        expect(text(view, view.entityId[row]), where).toBe(entity.id);
        const polygon = latest.geometry.polygon;
        const xs = polygon?.map((p) => p[0]) ?? [latest.geometry.box![0], latest.geometry.box![2]];
        const ys = polygon?.map((p) => p[1]) ?? [latest.geometry.box![1], latest.geometry.box![3]];
        const bounds = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
        expect([...view.box.subarray(row * 4, row * 4 + 4)], where).toEqual(
          bounds.map(Math.fround),
        );
        expect(view.polyIndex[row * 2 + 1], where).toBe(polygon?.length ?? 0);
        expect(view.motion[row], where).toBe(MOTION[entity.motion_state ?? ""] ?? Motion.None);
        const velocity = latest.measurements?.image_velocity;
        expect([...view.velocity.subarray(row * 2, row * 2 + 2)], where).toEqual(
          velocity ? [velocity.vx, velocity.vy].map(Math.fround) : [NaN, NaN],
        );
        // With several classes the primary is the best ranked one; `history` and the masks cover that case.
        const [only] = latest.classifications.length === 1 ? latest.classifications : [undefined];
        if (latest.classifications.length <= 1) {
          expect(text(view, view.classId[row]), where).toBe(only?.type ?? null);
          expect(view.score[row], where).toBe(only?.score ?? NaN);
          expect(view.confidence[row], where).toBe(only?.confidence ?? NaN);
        }
      });
      expect(view.polyPoints.length).toBe(
        2 *
          entities.reduce((n, e) => n + (e.observations.at(-1)!.geometry.polygon?.length ?? 0), 0),
      );
      const rowOf = (id: string): number => entities.findIndex((e) => e.id === id);
      expect(view.relationCount, name).toBe(scene.relations.length);
      scene.relations.forEach((relation, i) => {
        expect(text(view, view.relKind[i]), name).toBe(relation.type);
        expect([view.relSource[i], view.relTarget[i]], name).toEqual([
          rowOf(relation.source),
          rowOf(relation.target),
        ]);
      });
    }
  });

  it("gives each entity the class mask of all its classes and its colour lists", () => {
    // The goldens' policy: bit 0 humans, 1 vehicles, 2 heads, 3 unmapped classes, 4 unclassified.
    const mask = (name: string, id: string): number => {
      const view = readSample(bytes(`items/${name}.bin`));
      expect(view.maskWords).toBe(1);
      const ids = [...view.entityId].map((sid) => view.strings.string(sid));
      return view.classMask[ids.indexOf(id)];
    };
    expect(mask("classes", "car-1")).toBe(0b00010);
    expect(mask("classes", "unmapped-1")).toBe(0b01000);
    expect(mask("classes", "unclassified-1")).toBe(0b10000);
    expect(mask("classes", "dog-1")).toBe(0);
    // `ranked` is a car by score and also a human and a head.
    expect(mask("history", "ranked")).toBe(0b00111);

    const view = readSample(bytes("items/classes.bin"));
    const named = (name: string) => view.attrs.find((c) => view.strings.string(c.name) === name)!;
    const upper = named("upper_clothing_colors");
    const [first, count] = [upper.span![0], upper.span![1]];
    expect(count).toBe(2);
    expect(first).toBe(0);
    expect(view.colors.rgba[first]).toBe(0xc81e1eff);
    expect(view.colors.score[first]).toBe(0.9);
    expect(view.strings.string(view.colors.name[first + 1])).toBe("blue");
    expect(upper.span![2 * 3]).toBe(-1); // bus-1 has no clothing colours: missing, not empty
    const occluded = named("occluded");
    expect([occluded.bool![0], occluded.bool![1], occluded.bool![3]]).toEqual([0, 1, 2]);
  });

  it("views the received bytes, copies an unaligned item once and refuses what is not an item", () => {
    const golden = bytes("items/polygons.bin");
    expect(readSample(golden).box.buffer).toBe(golden);

    const shifted = new Uint8Array(golden.byteLength + 4);
    shifted.set(new Uint8Array(golden), 4);
    const copy = readSample(shifted.subarray(4));
    expect(copy.rows).toBe(4);
    expect(copy.box.buffer).not.toBe(shifted.buffer);

    const damaged = (change: (b: Uint8Array) => void): ArrayBuffer => {
      const b = new Uint8Array(golden.slice(0));
      change(b);
      return b.buffer;
    };
    const refused = [
      golden.slice(0, 64),
      golden.slice(0, golden.byteLength - 8),
      damaged((b) => (b[0] = 0)),
      damaged((b) => (b[4] = 2)),
      damaged((b) => (b[48 + 3] = 0xff)),
      damaged((b) => (b[36] = 4)),
    ];
    for (const bad of refused) expect(() => readSample(bad)).toThrow(SampleFormatError);
  });

  it("refuses references inside the item that point nowhere, instead of decoding or drawing them", () => {
    const golden = bytes("items/classes.bin");
    const words = new Uint32Array(golden);
    const section = (n: number): number => words[12 + n] / 4;
    const damaged = (change: (w: Uint32Array) => void): ArrayBuffer => {
      const copy = golden.slice(0);
      change(new Uint32Array(copy));
      return copy;
    };
    expect(() => readSample(golden)).not.toThrow();
    // An end offset of a middle string far past the bytes: it once decoded a million code points.
    expect(() => readSample(damaged((w) => (w[section(17) + 21] = 1_000_000)))).toThrowError(
      SampleFormatError,
    );
    // A string offset that goes back.
    expect(() =>
      readSample(damaged((w) => (w[section(17) + 5] = w[section(17) + 4] - 1))),
    ).toThrowError(SampleFormatError);
    // An entity or a class the item does not have, and an outline outside the points.
    for (const [n, value] of [
      [0, 99_999],
      [6, 99_999],
      [2, 99_999],
    ]) {
      expect(() => readSample(damaged((w) => (w[section(n)] = value)))).toThrowError(
        SampleFormatError,
      );
    }
    // Bytes that are not UTF-8 in the string table.
    expect(() =>
      readSample(
        damaged((w) => {
          const bytes = new Uint8Array(w.buffer);
          bytes[section(18) * 4 + 30] = 0xff;
        }),
      ),
    ).toThrowError(SampleFormatError);
  });
});
