/** Splits a shortcut such as "Ctrl+Shift+S" or "Ctrl++" into its keys. */
export function keyParts(shortcut: string): string[] {
  const key = shortcut.endsWith("+") ? "+" : (shortcut.split("+").pop() ?? "");
  const modifiers = shortcut
    .slice(0, shortcut.length - key.length)
    .split("+")
    .filter(Boolean);
  return [...modifiers, key];
}

/** Whether a key press is this shortcut. Shift is only compared for letters and function keys. */
export function matchesShortcut(event: KeyboardEvent, shortcut: string): boolean {
  const parts = keyParts(shortcut);
  const key = parts[parts.length - 1] ?? "";
  const has = (modifier: string) => parts.slice(0, -1).includes(modifier);
  const symbol = key.length === 1 && !/[a-z0-9]/i.test(key);
  return (
    event.key.toLowerCase() === key.toLowerCase() &&
    event.ctrlKey === has("Ctrl") &&
    event.altKey === has("Alt") &&
    (symbol || event.shiftKey === has("Shift"))
  );
}
