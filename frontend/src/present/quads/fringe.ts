/** How much a corner's offset may reach, in times the distance its edges move, before a sharp corner is cut short. */
const MITER_LIMIT = 4;
/** How much of the gap between two parts of the outline the corners facing it may use up together, less than all. */
const GAP_SHARE = 0.45;
/** How far beside an edge's middle the probe sits, in target pixels. */
const PROBE = 1e-3;

let scratch = new Float64Array(0);
let gaps = new Float64Array(0);

/** Corner floats `cornerOffsets` writes: the offset vector (x, y) and the room. */
export const CORNER_FLOATS = 3;

/**
 * The offset vector and the room of every corner of the closed polygon `xy` (x, y pairs, `count` corners), into `out`
 * as `CORNER_FLOATS` floats each. The unit normals of its two edges point away from the odd-even filled side, and the
 * corner moves along their mitered sum, which is a unit normal for a straight edge. A corner moved by this vector
 * times a distance moves each edge by that distance. Which side of an edge is filled is found with a probe just
 * beside its middle and the parity of the edges a ray from the probe crosses, so each lobe of a self-crossing polygon
 * has its own outside.
 *
 * The room is how far the edges may move inward at the corner without the moved outline crossing itself. Where two
 * parts of the outline are a gap apart, each moves less than half of it: the corner nearest the other part by
 * its offset, the part itself by its edges' distance, so a narrow bridge keeps a thickness instead of inverting.
 * Costs the square of `count`, twice.
 */
export function cornerOffsets(xy: ArrayLike<number>, count: number, out: Float64Array): void {
  if (scratch.length < count * 2) scratch = new Float64Array(count * 2);
  if (gaps.length < count * 2) gaps = new Float64Array(count * 2);
  const normals = scratch;
  for (let edge = 0; edge < count; edge++) {
    const next = (edge + 1) % count;
    const dx = xy[next * 2] - xy[edge * 2];
    const dy = xy[next * 2 + 1] - xy[edge * 2 + 1];
    const length = Math.max(Math.hypot(dx, dy), 1e-12);
    let nx = dy / length;
    let ny = -dx / length;
    const px = xy[edge * 2] + dx / 2 - nx * PROBE;
    const py = xy[edge * 2 + 1] + dy / 2 - ny * PROBE;
    let crossings = 0;
    for (let other = 0; other < count; other++) {
      const after = (other + 1) % count;
      const y0 = xy[other * 2 + 1];
      const y1 = xy[after * 2 + 1];
      if (y0 > py === y1 > py) continue;
      const x0 = xy[other * 2];
      if (x0 + ((py - y0) * (xy[after * 2] - x0)) / (y1 - y0) > px) crossings++;
    }
    // The probe is on the filled side when the crossings are odd, so the normal points out the other way.
    if (crossings % 2 === 0) {
      nx = -nx;
      ny = -ny;
    }
    normals[edge * 2] = nx;
    normals[edge * 2 + 1] = ny;
  }
  for (let corner = 0; corner < count; corner++) {
    const before = (corner + count - 1) % count;
    const nx = normals[corner * 2];
    const ny = normals[corner * 2 + 1];
    let dx = normals[before * 2] + nx;
    let dy = normals[before * 2 + 1] + ny;
    const length = Math.max(Math.hypot(dx, dy), 1e-12);
    dx /= length;
    dy /= length;
    const scale = 1 / Math.max(dx * nx + dy * ny, 1 / MITER_LIMIT);
    out[corner * CORNER_FLOATS] = dx * scale;
    out[corner * CORNER_FLOATS + 1] = dy * scale;
    gaps[corner * 2] = Infinity;
    gaps[corner * 2 + 1] = Infinity;
  }
  // gaps: per corner, the squared distance to the nearest edge it is not on, and to the nearest corner not on its edges.
  for (let corner = 0; corner < count; corner++) {
    const x = xy[corner * 2];
    const y = xy[corner * 2 + 1];
    let nearest = Infinity;
    for (let edge = 0; edge < count; edge++) {
      const next = edge + 1 === count ? 0 : edge + 1;
      // The two edges meeting at the corner move with it.
      if (edge === corner || next === corner) continue;
      const x0 = xy[edge * 2];
      const y0 = xy[edge * 2 + 1];
      const dx = xy[next * 2] - x0;
      const dy = xy[next * 2 + 1] - y0;
      const along = Math.max(
        0,
        Math.min(1, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy || 1)),
      );
      const ex = x - (x0 + along * dx);
      const ey = y - (y0 + along * dy);
      const squared = ex * ex + ey * ey;
      if (squared < nearest) nearest = squared;
      if (squared < gaps[edge * 2 + 1]) gaps[edge * 2 + 1] = squared;
      if (squared < gaps[next * 2 + 1]) gaps[next * 2 + 1] = squared;
    }
    gaps[corner * 2] = nearest;
  }
  for (let corner = 0; corner < count; corner++) {
    const reach = Math.hypot(out[corner * CORNER_FLOATS], out[corner * CORNER_FLOATS + 1]);
    out[corner * CORNER_FLOATS + 2] =
      GAP_SHARE *
      Math.min(Math.sqrt(gaps[corner * 2]) / Math.max(reach, 1), Math.sqrt(gaps[corner * 2 + 1]));
  }
}
