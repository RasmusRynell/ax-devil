/**
 * Puts `text` on the clipboard and says whether it got there. Where the page may not use the clipboard API (an
 * insecure origin, as in a browser that joined a shared session over plain HTTP) it copies through a hidden field.
 * A provider lets a disposing owner release its retained string while a submitted native write is pending.
 * `current` prevents a superseded asynchronous copy from falling back after the clipboard API refuses.
 */
export async function writeClipboard(
  source: string | (() => string),
  current: () => boolean = () => true,
): Promise<boolean> {
  if (!current()) return false;
  const read = () => (typeof source === "function" ? source() : source);
  let text = read();
  try {
    const writing = navigator.clipboard.writeText(text);
    text = "";
    await writing;
    return true;
  } catch {
    if (!current()) return false;
    text = read();
    const field = document.createElement("textarea");
    const focused = document.activeElement;
    field.value = text;
    document.body.append(field);
    field.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      field.remove();
      if (focused instanceof HTMLElement) focused.focus();
    }
  }
}
