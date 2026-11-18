import { type Scalar, dynamic } from "./compiled";
import type { EvalContext } from "./context";
import { through } from "./fields";
import type { Side } from "./ir";

/** The color with its alpha byte set to `alpha` (rounded, clamped to 0..255); null when either is null. */
export function withAlpha(color: Scalar, alpha: Scalar): Scalar {
  const [c, a] = [color.fn, alpha.fn];
  return dynamic((row) => {
    const value = c(row);
    const byte = a(row);
    if (value !== value || byte !== byte) return Number.NaN;
    return ((value & 0xffffff00) | Math.min(255, Math.max(0, Math.round(byte)))) >>> 0;
  });
}

/** Stops of a color ramp, strictly increasing in `at`, colors as 0xRRGGBBAA. */
export interface Stops {
  readonly at: readonly number[];
  readonly color: readonly number[];
}

/** The color on a ramp: clamped at the ends, per-channel linear between stops, each channel `floor(c + 0.5)`. */
export function ramp(value: Scalar, stops: Stops): Scalar {
  const { at, color } = stops;
  const last = at.length - 1;
  const channels = [24, 16, 8, 0].map((shift) =>
    Float64Array.from(color, (c) => Math.floor(c / 2 ** shift) % 256),
  );
  const [red, green, blue, alpha] = channels;
  const read = value.fn;
  return dynamic((row) => {
    const x = read(row);
    if (!Number.isFinite(x)) return Number.NaN;
    if (x <= at[0]) return color[0];
    let i = 1;
    while (i < last && x > at[i]) i++;
    if (x > at[i]) return color[last];
    const t = (x - at[i - 1]) / (at[i] - at[i - 1]);
    const u = 1 - t;
    const r = Math.floor(red[i - 1] * u + red[i] * t + 0.5);
    const g = Math.floor(green[i - 1] * u + green[i] * t + 0.5);
    const b = Math.floor(blue[i - 1] * u + blue[i] * t + 0.5);
    const a = Math.floor(alpha[i - 1] * u + alpha[i] * t + 0.5);
    return ((r * 256 + g) * 256 + b) * 256 + a;
  });
}

/** The palette entry the CRC-32 of the string's UTF-8 bytes selects, so a track keeps its color in every run. */
export function palette(ctx: EvalContext, id: Scalar, colors: readonly number[]): Scalar {
  const read = id.fn;
  const count = colors.length;
  return dynamic((row) => {
    const value = read(row);
    return value < 0 ? Number.NaN : colors[ctx.strings.crc32(value) % count];
  });
}

/** The color of the highest-scored entry of an attribute's color list (the first on ties); null when empty. */
export function top(ctx: EvalContext, slot: number, side: Side): Scalar {
  const read = through(
    ctx,
    side,
    (entity) => {
      const span = ctx.attrSpan[slot];
      const first = span[2 * entity];
      const count = span[2 * entity + 1];
      if (first < 0 || count <= 0) return Number.NaN;
      const end = first + count;
      let best = first;
      for (let i = first + 1; i < end; i++) if (ctx.colorScore[i] > ctx.colorScore[best]) best = i;
      return ctx.colorRgba[best];
    },
    Number.NaN,
  );
  return dynamic(read);
}
