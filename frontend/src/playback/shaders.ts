/** The two shaders that draw a frame into a lane's cell. */

/** Puts the quad on the part of the picture the cell shows, `crop` being its rectangle in the picture's 0..1 space. */
export const VERTEX = `#version 300 es
in vec2 corner;
out vec2 coord;
uniform vec4 crop;
void main() {
  coord = crop.xy + crop.zw * vec2(corner.x * .5 + .5, .5 - corner.y * .5);
  gl_Position = vec4(corner, 0., 1.);
}`;

/**
 * Converts to RGB. mode 0: planar Y, U, V. mode 1: Y, then interleaved UV in the second texture. mode 2: RGBA in the
 * first.
 */
export const FRAGMENT = `#version 300 es
precision highp float;
in vec2 coord;
out vec4 color;
uniform sampler2D plane0, plane1, plane2;
uniform int mode;
uniform float kr, kb, limited;
void main() {
  // Every plane is read at the centre of the source pixel under this one, at any zoom: the picture is converted to RGB
  // at its own size (chroma interpolated there), then enlarged nearest.
  vec2 size = vec2(textureSize(plane0, 0));
  vec2 uv = (min(floor(coord * size), size - 1.) + .5) / size;
  if (mode == 2) { color = vec4(texture(plane0, uv).rgb, 1.); return; }
  float y = texture(plane0, uv).r;
  vec2 chroma = mode == 1 ? texture(plane1, uv).rg : vec2(texture(plane1, uv).r, texture(plane2, uv).r);
  float u = chroma.x - .5, v = chroma.y - .5;
  if (limited > .5) { y = (y - 16. / 255.) * 255. / 219.; u *= 255. / 224.; v *= 255. / 224.; }
  float r = y + 2. * (1. - kr) * v;
  float b = y + 2. * (1. - kb) * u;
  float g = (y - kr * r - kb * b) / (1. - kr - kb);
  color = vec4(r, g, b, 1.);
}`;
