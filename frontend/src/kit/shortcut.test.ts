import { describe, expect, it } from "vite-plus/test";
import keys from "../../../fixtures/shortcuts/keys.json";
import { keyParts, shortcutOf } from "./shortcut";

/** The key press that types a canonical shortcut. */
function press(shortcut: string): KeyboardEvent {
  const parts = keyParts(shortcut);
  const name = parts[parts.length - 1] ?? "";
  const shift = parts.includes("Shift");
  const key =
    { Space: " ", Left: "ArrowLeft", Right: "ArrowRight", Up: "ArrowUp", Down: "ArrowDown" }[
      name
    ] ?? (name.length === 1 && !shift ? name.toLowerCase() : name);
  return new KeyboardEvent("keydown", {
    key,
    ctrlKey: parts.includes("Ctrl"),
    altKey: parts.includes("Alt"),
    metaKey: parts.includes("Meta"),
    shiftKey: shift,
  });
}

describe("shortcutOf", () => {
  it("writes every key press the backend accepts in the backend's canonical form", () => {
    const accepted = keys
      .map((entry) => entry.canonical)
      .filter((canonical): canonical is string => canonical !== undefined);
    expect(accepted.length).toBeGreaterThan(20);
    for (const shortcut of accepted) expect(shortcutOf(press(shortcut))).toBe(shortcut);
  });

  it("uses Qt portable names for macOS Command and Control, and native chip order", () => {
    for (const [held, expected] of [
      [{ ctrlKey: true }, "Meta+S"],
      [{ metaKey: true }, "Ctrl+S"],
    ] as const)
      expect(shortcutOf(new KeyboardEvent("keydown", { key: "s", ...held }), "MacIntel")).toBe(
        expected,
      );
    expect(keyParts("Meta+Ctrl+Alt+Shift+S", "MacIntel")).toEqual(["⌃", "⌥", "⇧", "⌘", "S"]);
  });

  it("gives nothing until the press is a whole shortcut, and drops Shift from symbols", () => {
    const of = (init: KeyboardEventInit) => shortcutOf(new KeyboardEvent("keydown", init));
    expect(of({ key: "Control", ctrlKey: true })).toBeNull();
    expect(of({ key: "Meta", metaKey: true })).toBeNull();
    expect(of({ key: "å" })).toBeNull();
    expect(of({ key: ">", shiftKey: true })).toBe(">");
    // Keypad Enter is Enter, not a key of its own that would record as nothing.
    expect(of({ key: "Enter", code: "NumpadEnter", ctrlKey: true })).toBe("Ctrl+Enter");
  });
});
