import { timecode } from "./timecode";

/** What `Opened` says about the video that the pane header shows. */
type Described = {
  width: number;
  height: number;
  fps: number | null;
  frame_count: number;
  duration_us: number;
};

/** Whether `digits` rounds up when cut before `rest`, a tie going to the even last digit of `digits`. */
function roundsUp(digits: string, rest: string): boolean {
  const next = rest[0] ?? "0";
  return (
    next > "5" || (next === "5" && (/[1-9]/.test(rest.slice(1)) || Number(digits.at(-1)) % 2 === 1))
  );
}

/** Python's `format(x, "g")` for a positive `x` below 1e21: six significant digits of its exact value, a tie to even. */
function formatG(x: number): string {
  if (x === 0) return "0";
  const [whole, fraction] = x.toFixed(100).split(".");
  const digits = (whole + fraction).replace(/^0+/, "");
  let exponent =
    whole === "0" ? -(fraction.length - fraction.replace(/^0+/, "").length + 1) : whole.length - 1;
  let sig = digits.slice(0, 6).padEnd(6, "0");
  if (roundsUp(sig, digits.slice(6))) sig = String(Number(sig) + 1);
  if (sig.length > 6) {
    sig = sig.slice(0, 6);
    exponent += 1;
  }
  const trimmed = (text: string) => text.replace(/0+$/, "");
  if (exponent < -4 || exponent >= 6) {
    const mantissa = [sig[0], trimmed(sig.slice(1))].filter(Boolean).join(".");
    return `${mantissa}e${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(2, "0")}`;
  }
  const shown =
    exponent < 0
      ? `0.${"0".repeat(-exponent - 1)}${sig}`
      : `${sig.slice(0, exponent + 1)}.${sig.slice(exponent + 1)}`;
  return shown.replace(/0+$/, "").replace(/\.$/, "");
}

/**
 * Python's `f"{round(x, 2):g}"` for a positive `x`: the double's exact decimal value rounded to two places, a tie to
 * the even digit, then `:g`. `toFixed` is exact too but sends a tie up, so a tie is found in the exact digits
 * (`toFixed(100)` shows them all) and kept down when the last kept digit is even.
 */
function roundedRate(x: number): string {
  const exact = x.toFixed(100);
  const cut = exact.indexOf(".") + 3;
  const tie = exact[cut] === "5" && /^0*$/.test(exact.slice(cut + 1));
  const kept = exact.slice(0, cut);
  return formatG(Number(tie && Number(kept.at(-1)) % 2 === 0 ? kept : x.toFixed(2)));
}

/** The video's size, frame rate and length for the pane header, such as `640×480 · 25 fps · 0:04`; empty when unknown. */
export function videoDetails({ width, height, fps, frame_count, duration_us }: Described): string {
  const parts: string[] = [];
  if (width > 0 && height > 0) parts.push(`${width}×${height}`);
  if (fps !== null && fps > 0) {
    parts.push(`${roundedRate(fps)} fps`);
    // A video that reports no length (one frame) is as long as its frames at the rate.
    const lengthUs = duration_us > 0 ? duration_us : (frame_count / fps) * 1_000_000;
    parts.push(timecode(lengthUs, false));
  }
  return parts.join(" · ");
}
