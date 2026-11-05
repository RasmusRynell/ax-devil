/** How far a corner's offset may reach, in times the fringe, before a sharp corner is cut short. */
const MITER_LIMIT = 4;
/** How far beside an edge's middle the probe sits, in target pixels. */
const PROBE = 1e-3;

let scratch = new Float64Array(0);

/**
 * The offset vector of every corner of the closed polygon `xy` (x, y pairs, `count` corners): the unit normals of its
 * two edges point away from the odd-even filled side, and the corner moves along their mitered sum, which is a unit
 * normal for a straight edge. A corner moved by this vector times a distance moves each edge by that distance.
 * Which side of an edge is filled is found with a probe just beside its middle and the parity of the edges a ray
 * from the probe crosses, so each lobe of a self-crossing polygon has its own outside. Costs the square of `count`.
 */
export function cornerOffsets(xy: ArrayLike<number>, count: number, out: Float64Array): void {
  if (scratch.length < count * 2) scratch = new Float64Array(count * 2);
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
    out[corner * 2] = dx * scale;
    out[corner * 2 + 1] = dy * scale;
  }
}
