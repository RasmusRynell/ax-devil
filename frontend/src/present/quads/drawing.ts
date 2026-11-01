import type { LayoutView } from "../layout/sink";
import type { LaneQuads } from "./lane";

/**
 * The lane's quads as the painter takes a drawing today (`PreparedDrawing` of `overlay/prepare.ts`): everything in
 * draw order in `texts`, which the painter draws after the polygon fills. Until the painter learns per-layer ranges
 * (`LaneQuads.layerQuads`), layer order only holds among quads; fills still go under all of them. The arrays are views
 * of the lane's memory: valid until it lays out again.
 */
export function drawingOf(lane: LaneQuads, view: LayoutView, generation: number, opacity = 1) {
  return {
    generation,
    shapes: new Float32Array(0),
    texts: lane.quads.subarray(),
    polygons: lane.fills.view(),
    uploads: [] as never[],
    opacity,
    target: { width: view.width, height: view.height },
    dpr: view.dpr,
  };
}
