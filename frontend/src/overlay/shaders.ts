/**
 * The overlay's GLSL, ported from the reference's overlay material. Quads arrive in target CSS pixels and are
 * placed onto the picture by `offset` and `scale` (device pixels); the fragment shader draws each by its kind with
 * signed-distance edges one device pixel wide. Colors are premultiplied.
 */

import { PAD } from "./quads";

/** Places a target pixel position on the canvas. */
const PLACE = `
uniform vec2 canvas, offset, scale;
vec4 place(vec2 position) {
  vec2 device = offset + position * scale;
  return vec4(device.x / canvas.x * 2. - 1., 1. - device.y / canvas.y * 2., 0., 1.);
}`;

/** One instanced quad per shape, glyph or label part (layout in quads.ts). */
export const QUAD_VERTEX = `#version 300 es
layout(location = 0) in vec2 origin;
layout(location = 1) in vec2 axis;
layout(location = 2) in vec2 size;
layout(location = 3) in vec2 radiusStroke;
layout(location = 4) in vec2 dashKind;
layout(location = 5) in vec4 fill;
layout(location = 6) in vec4 pen;
out vec2 vUv;
out vec2 vSize;
out vec2 vRadiusStroke;
out vec2 vDashKind;
out vec4 vFill;
out vec4 vPen;
// x: one over the atlas page size, y: the device pixel ratio the glyphs were rasterized at.
uniform vec2 atlas;
${PLACE}
void main() {
  vec2 corner = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  vec2 local;
  if (abs(dashKind.y - 3.) < .5) {
    local = corner * size;
    vUv = (radiusStroke + corner * size * atlas.y) * atlas.x;
  } else {
    local = -${PAD.toFixed(1)} + corner * (size + 2. * ${PAD.toFixed(1)});
    vUv = local;
  }
  vSize = size;
  vRadiusStroke = radiusStroke;
  vDashKind = dashKind;
  vFill = fill;
  vPen = pen;
  gl_Position = place(origin + axis * local.x + vec2(-axis.y, axis.x) * local.y);
}`;

// Kinds (vDashKind.y): 0 rounded rect, 1 ellipse, 2 line (rotated rect, dashed along u), 3 glyph. Kinds 0-2: vUv is
// the position inside the shape and vSize its size; a line's vRadiusStroke.x is its cap radius and .y its dash
// phase, otherwise they are the corner radius and the inward stroke. Kind 3: vUv is the atlas page coordinate.
export const QUAD_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv;
in vec2 vSize;
in vec2 vRadiusStroke;
in vec2 vDashKind;
in vec4 vFill;
in vec4 vPen;
out vec4 color;
uniform sampler2D page;
const float PI = 3.14159265;

// Edge coverage from a signed distance; pixel is the size of one device pixel in shape units.
float coverage(float distance, float pixel) { return clamp(.5 - distance / pixel, 0., 1.); }

float roundedRectDistance(vec2 p, vec2 halfSize, float radius) {
  vec2 q = abs(p) - (halfSize - vec2(radius));
  return length(max(q, 0.)) + min(max(q.x, q.y), 0.) - radius;
}

float ellipseDistance(vec2 p, vec2 h) {
  float k0 = length(p / h);
  float k1 = length(p / (h * h));
  if (k1 < 1e-6) return -min(h.x, h.y);
  return k0 * (k0 - 1.) / k1;
}

// Clockwise distance along a rounded rect outline from the start of its top edge.
float perimeter(vec2 p, vec2 halfSize, float radius) {
  vec2 c = halfSize - vec2(radius);
  float a = 2. * c.x;
  float b = 2. * c.y;
  float arc = .5 * PI * radius;
  vec2 v;
  if (p.x > c.x && p.y < -c.y) { v = p - vec2(c.x, -c.y); return a + atan(v.x, max(-v.y, 1e-6)) * radius; }
  if (p.x > c.x && p.y > c.y) { v = p - c; return a + arc + b + atan(v.y, max(v.x, 1e-6)) * radius; }
  if (p.x < -c.x && p.y > c.y) {
    v = p - vec2(-c.x, c.y);
    return 2. * a + b + 2. * arc + atan(-v.x, max(v.y, 1e-6)) * radius;
  }
  if (p.x < -c.x && p.y < -c.y) { v = p + c; return 2. * a + 2. * b + 3. * arc + atan(-v.y, max(-v.x, 1e-6)) * radius; }
  if (halfSize.y - abs(p.y) < halfSize.x - abs(p.x)) {
    if (p.y < 0.) return clamp(p.x + c.x, 0., a);
    return a + 2. * arc + b + clamp(c.x - p.x, 0., a);
  }
  if (p.x > 0.) return a + arc + clamp(p.y + c.y, 0., b);
  return 2. * a + b + 3. * arc + clamp(c.y - p.y, 0., b);
}

void main() {
  float kind = vDashKind.y;
  float dash = vDashKind.x;
  float stroke = vRadiusStroke.y;
  float pixel = max(length(dFdx(vUv)), 1e-4);
  vec4 ink;
  if (kind > 2.5) {
    ink = vFill * texture(page, vUv).r;
  } else {
    vec2 halfSize = .5 * vSize;
    vec2 p = vUv - halfSize;
    if (kind > 1.5) {
      float on = dash > 0. ? step(mod(vUv.x + stroke, dash), .5 * dash) : 1.;
      float cap = min(vRadiusStroke.x, halfSize.y);
      ink = vFill * (coverage(roundedRectDistance(p, halfSize, cap), pixel) * on);
    } else {
      float radius = min(vRadiusStroke.x, min(halfSize.x, halfSize.y));
      float d = kind > .5 ? ellipseDistance(p, halfSize) : roundedRectDistance(p, halfSize, radius);
      float inner = coverage(d + stroke, pixel);
      vec4 band = vPen;
      if (dash > 0. && stroke > 0.) {
        // Dashes follow the stroke's centre line; ellipses are circles and run clockwise from the top.
        float mid = .5 * stroke;
        float s = kind > .5
          ? mod(atan(p.x, -p.y), 2. * PI) * (halfSize.x - mid)
          : perimeter(p, halfSize - vec2(mid), max(radius - mid, 0.));
        band *= step(mod(s, dash), .5 * dash);
      }
      ink = (stroke > 0. ? mix(band, vFill, inner) : vFill) * coverage(d, pixel);
    }
  }
  color = ink;
}`;

/**
 * Polygon corners, drawn as triangle fans into the stencil and then filled where it is odd, and the faded ring around
 * them. A vertex moves by its offset along `edge`: x outward for the ring's faded side (`side` 1), y inward for the
 * filled one (`side` 0), so the polygon's true edge lies half way through the ring, where the coverage is one half.
 */
export const FILL_VERTEX = `#version 300 es
layout(location = 0) in vec2 corner;
layout(location = 1) in vec3 offsetSide;
uniform vec2 edge;
out float vCoverage;
${PLACE}
void main() {
  vCoverage = 1. - offsetSide.z;
  gl_Position = place(corner + offsetSide.xy * mix(-edge.y, edge.x, offsetSide.z));
}`;

export const FILL_FRAGMENT = `#version 300 es
precision mediump float;
uniform vec4 fill;
in float vCoverage;
out vec4 color;
void main() { color = fill * vCoverage; }`;

/** One triangle over the whole target, with no vertex data. */
export const COMPOSITE_VERTEX = `#version 300 es
void main() {
  vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(corner * 2. - 1., 0., 1.);
}`;

/** Copies a premultiplied layer of the target's size, one texel to one pixel, at `opacity`. */
export const COMPOSITE_FRAGMENT = `#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D layer;
uniform float opacity;
out vec4 color;
void main() { color = texelFetch(layer, ivec2(gl_FragCoord.xy), 0) * opacity; }`;

/** Compiles and links a program; throws with the driver's log when it cannot. */
export function compileProgram(
  gl: WebGL2RenderingContext,
  vertex: string,
  fragment: string,
): WebGLProgram {
  const created = gl.createProgram();
  if (!created) throw new Error("a program cannot be created");
  try {
    attachAll(gl, created, vertex, fragment);
  } catch (error) {
    gl.deleteProgram(created);
    throw error;
  }
  return created;
}

function attachAll(
  gl: WebGL2RenderingContext,
  created: WebGLProgram,
  vertex: string,
  fragment: string,
): void {
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("a shader cannot be created");
    try {
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(shader) ?? "a shader does not compile");
      gl.attachShader(created, shader);
    } finally {
      // An attached shader lives on until its program goes; one that failed goes now.
      gl.deleteShader(shader);
    }
  }
  gl.linkProgram(created);
  if (!gl.getProgramParameter(created, gl.LINK_STATUS))
    throw new Error(gl.getProgramInfoLog(created) ?? "a program does not link");
}
