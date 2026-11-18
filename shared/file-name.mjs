/** A suggested name as one visible, portable filename, using Python's separator replacement. */
export function safeFileName(name, suffix = "") {
  let file = name
    .replace(/[/\\<>:"|?*\p{Cc}]/gu, "-")
    .replace(/\.{2,}/g, "-")
    .replace(/^\.+/, "-")
    .replace(/[. ]+$/g, "");
  if (!file) file = "-";
  if (/^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(file)) file = `-${file}`;
  const encoder = new TextEncoder();
  const budget = 255 - encoder.encode(suffix).length;
  let truncated = "";
  let bytes = 0;
  for (const character of file) {
    bytes += encoder.encode(character).length;
    if (bytes > budget) break;
    truncated = `${truncated}${character}`;
  }
  return `${truncated.replace(/[. ]+$/g, "") || "-"}${suffix}`;
}

/** A chosen path's folder (including its final separator) and filename, with either platform's separators. */
export function splitPath(path) {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return { folder: path.slice(0, cut + 1), name: path.slice(cut + 1) };
}
