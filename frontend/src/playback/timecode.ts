/** `us / unit` rounded half to even, as Python's `round`, in whole numbers so a tie is exact. */
function roundedDiv(us: number, unit: number): number {
  const whole = Math.floor(us / unit);
  const rest = us - whole * unit;
  return whole + (2 * rest > unit || (2 * rest === unit && whole % 2 === 1) ? 1 : 0);
}

/**
 * Microseconds as `m:ss.cc`, or `h:mm:ss.cc` from one hour; `m:ss` without `hundredths`. Rounds half to even like
 * Python, so a time reads the same in both apps.
 */
export function timecode(us: number, hundredths = true): string {
  const clamped = Math.max(0, us);
  const total = hundredths ? roundedDiv(clamped, 10_000) : roundedDiv(clamped, 1_000_000) * 100;
  const minutes = Math.floor(total / 6000) % 60;
  const hours = Math.floor(total / 360_000);
  const whole = String(Math.floor(total / 100) % 60).padStart(2, "0");
  const seconds = hundredths ? `${whole}.${String(total % 100).padStart(2, "0")}` : whole;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`
    : `${minutes}:${seconds}`;
}
