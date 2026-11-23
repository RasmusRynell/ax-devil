import { type Diagnostics, didYouMean } from "../expr/diagnostic";

/** A JSON pointer to `key` inside the value at `base`. */
export function pointerTo(base: string, key: string | number): string {
  return `${base}/${String(key).replaceAll("~", "~0").replaceAll("/", "~1")}`;
}

const at = (pointer: string) => ({ pointer, spans: false });

/** The value as an object, or a recorded problem and null. */
export function objectAt(
  value: unknown,
  pointer: string,
  diag: Diagnostics,
): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value))
    return value as Record<string, unknown>;
  diag.add("value", at(pointer), null, "expected an object");
  return null;
}

/** The value as an array, or a recorded problem and null. */
export function arrayAt(value: unknown, pointer: string, diag: Diagnostics): unknown[] | null {
  if (Array.isArray(value)) return value;
  diag.add("value", at(pointer), null, "expected a list");
  return null;
}

/** The value as a string, or a recorded problem and null. */
export function stringAt(value: unknown, pointer: string, diag: Diagnostics): string | null {
  if (typeof value === "string") return value;
  diag.add("value", at(pointer), null, "expected a string");
  return null;
}

/** An object with exactly the allowed keys: unknown ones are problems (with a suggestion), required ones must be there. */
export function closedObject(
  value: unknown,
  pointer: string,
  allowed: readonly string[],
  required: readonly string[],
  diag: Diagnostics,
): Record<string, unknown> | null {
  const object = objectAt(value, pointer, diag);
  if (!object) return null;
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      diag.add(
        "unknown-key",
        at(pointerTo(pointer, key)),
        null,
        `unknown key '${key}'${didYouMean(key, allowed)}`,
      );
    }
  }
  for (const key of required) {
    if (!(key in object)) diag.add("missing-key", at(pointer), null, `missing key '${key}'`);
  }
  return object;
}
