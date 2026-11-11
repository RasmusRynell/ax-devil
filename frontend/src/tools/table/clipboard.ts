/**
 * Puts `text` on the clipboard and says whether it got there. Where the page may not use the clipboard API (an
 * insecure origin, as in a browser that joined a shared session over plain HTTP) it copies through a hidden field.
 * `current` prevents a superseded asynchronous copy from falling back after the clipboard API refuses.
 */
export async function writeClipboard(
  text: string,
  current: () => boolean = () => true,
): Promise<boolean> {
  if (!current()) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (!current()) return false;
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
