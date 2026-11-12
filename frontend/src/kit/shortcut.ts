/** Splits portable shortcut text into keys; a Mac platform uses Qt native modifier symbols and order. */
export function keyParts(shortcut: string, platform = ""): string[] {
  const key = shortcut.endsWith("+") ? "+" : (shortcut.split("+").pop() ?? "");
  const modifiers = shortcut
    .slice(0, shortcut.length - key.length)
    .split("+")
    .filter(Boolean);
  if (platform.startsWith("Mac")) {
    const symbols: Record<string, string> = { Meta: "⌃", Alt: "⌥", Shift: "⇧", Ctrl: "⌘" };
    return [
      ...["Meta", "Alt", "Shift", "Ctrl"]
        .filter((name) => modifiers.includes(name))
        .map((name) => symbols[name] ?? name),
      key,
    ];
  }
  return [...modifiers, key];
}

/** The names a shortcut gives keys whose `event.key` is not a readable character. */
const KEY_NAMES: Record<string, string> = {
  " ": "Space",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  ArrowUp: "Up",
  ArrowDown: "Down",
};

const NAMED_KEYS = new Set([
  "Enter",
  "Escape",
  "Tab",
  "Backspace",
  "Delete",
  "Insert",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

/**
 * The shortcut a key press is, in the canonical form the backend stores (`Meta+Ctrl+Alt+Shift+<key>`, letters upper case),
 * or null while it is not a complete shortcut: a modifier alone or a key the grammar has no name for.
 * Shift is left out for symbol keys, whose `event.key` already is the shifted character.
 */
export function shortcutOf(event: KeyboardEvent, platform = navigator.platform): string | null {
  const name = KEY_NAMES[event.key] ?? event.key;
  const symbol = name.length === 1 && !/[a-z0-9]/i.test(name);
  const known =
    NAMED_KEYS.has(name) ||
    KEY_NAMES[event.key] !== undefined ||
    /^F([1-9]|1\d|2[0-4])$/.test(name) ||
    (name.length === 1 && /[\x21-\x7e]/.test(name));
  if (!known) return null;
  // Qt's portable Ctrl is Command on macOS; portable Meta is physical Control.
  const mac = platform.startsWith("Mac");
  const modifiers = [
    (mac ? event.ctrlKey : event.metaKey) && "Meta",
    (mac ? event.metaKey : event.ctrlKey) && "Ctrl",
    event.altKey && "Alt",
    event.shiftKey && !symbol && "Shift",
  ].filter(Boolean);
  return [...modifiers, name.length === 1 ? name.toUpperCase() : name].join("+");
}
