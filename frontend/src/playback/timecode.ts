/** `x` rounded to the nearest whole number, a tie to the even one, as Python's `round`. */
function roundHalfEven(x: number): number {
  const whole = Math.floor(x);
  const rest = x - whole;
  return rest > 0.5 || (rest === 0.5 && whole % 2 === 1) ? whole + 1 : whole;
}

/**
 * Microseconds as `m:ss.cc`, or `h:mm:ss.cc` from one hour; `m:ss` without `hundredths`. It does the reference's
 * arithmetic on the same doubles, `round(seconds * 100)` or `round(seconds) * 100` with `seconds = us / 1e6`, so
 * a time reads the same in both apps, float error included: 545000 us is 0:00.55 and 575000 us is 0:00.57.
 */
export function timecode(us: number, hundredths = true): string {
  const seconds = Math.max(0, us) / 1_000_000;
  const total = hundredths ? roundHalfEven(seconds * 100) : roundHalfEven(seconds) * 100;
  const minutes = Math.floor(total / 6000) % 60;
  const hours = Math.floor(total / 360_000);
  const whole = String(Math.floor(total / 100) % 60).padStart(2, "0");
  const rest = hundredths ? `${whole}.${String(total % 100).padStart(2, "0")}` : whole;
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}
