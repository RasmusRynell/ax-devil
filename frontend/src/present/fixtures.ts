import type { SampleView } from "./columns";
import { type FixtureEntity, type FixtureRelation, makeSample } from "./expr/sampleFixture";

/**
 * A hand-written sample with every column the evaluator and layout read (no polygons). `makeSample` covers what
 * expressions read; this adds the polygon and colour-name columns. For tests and benchmarks only.
 */
export function fullSample(
  entities: readonly FixtureEntity[],
  relations: readonly FixtureRelation[] = [],
): SampleView {
  const sample = makeSample(entities, relations);
  return {
    ...sample,
    polyIndex: new Uint32Array(sample.rows * 2),
    polyPoints: new Float32Array(0),
    colors: {
      rgba: sample.colors.rgba,
      score: Float64Array.from(sample.colors.score),
      name: new Int32Array(sample.colors.rgba.length).fill(-1),
    },
  } as unknown as SampleView;
}

const CLASSES = ["car", "human", "head", "bus", "dog", "truck"];

/** `count` objects spread over the frame with ids that depend on `variant`, in the mix the built-in catalogs read. */
export function syntheticSample(count: number, variant = 0): SampleView {
  let seed = 12345 + variant;
  const next = (): number => {
    seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
    return seed / 2 ** 32;
  };
  const entities: FixtureEntity[] = [];
  for (let i = 0; i < count; i++) {
    const w = 0.01 + next() * 0.08;
    const h = 0.02 + next() * 0.15;
    const x = next() * (1.02 - w) - 0.01;
    const y = next() * (1.02 - h) - 0.01;
    entities.push({
      id: `track-${variant}-${i * 7919 + variant}`,
      class: CLASSES[i % CLASSES.length],
      score: next(),
      confidence: next(),
      motion: i % 3 === 0 ? "moving" : "stationary",
      velocity: [(next() - 0.5) * 0.02, (next() - 0.5) * 0.02],
      box: [x, y, x + w, y + h],
      attrs: {
        occluded: i % 4 === 0,
        carries_bag: i % 3 === 0,
        face_visible: (i % 10) / 10,
        vehicle_colors: [
          { rgba: 0xff0000ff, score: 0.4 },
          { rgba: 0x0000ffff, score: 0.9 },
        ],
        upper_clothing_colors: [{ rgba: 0x00ff00ff, score: 0.5 }],
        lower_clothing_colors: [],
      },
    });
  }
  return fullSample(entities);
}
