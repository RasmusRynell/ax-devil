/** Counts UTF-8 bytes, stopping as soon as a supplied bound is exceeded. */
export function utf8Bytes(text: string, bound = Number.POSITIVE_INFINITY): number {
  let bytes = 0;
  for (const character of text) {
    const code = character.codePointAt(0) ?? 0;
    bytes += code <= 0x7f ? 1 : code <= 0x7ff ? 2 : code <= 0xffff ? 3 : 4;
    if (bytes > bound) break;
  }
  return bytes;
}

/** The largest UTF-8 prefix within a byte budget, cut only between code points. */
export function utf8Prefix(text: string, bound: number): { text: string; bytes: number } {
  let bytes = 0;
  let end = 0;
  for (const character of text) {
    const size = utf8Bytes(character);
    if (bytes + size > bound) break;
    bytes += size;
    end += character.length;
  }
  // Copy a cut prefix so a saved pin cannot retain a large backing string.
  const prefix =
    end === text.length
      ? text
      : new TextDecoder().decode(new TextEncoder().encode(text.slice(0, end)));
  return { text: prefix, bytes };
}
