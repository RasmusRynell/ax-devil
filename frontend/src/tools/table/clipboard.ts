/**
 * Puts `text` on the clipboard and says whether it got there. Where the page may not use the clipboard API (an
 * insecure origin, as in a browser that joined a shared session over plain HTTP) it copies through a hidden field.
 */
export async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
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
