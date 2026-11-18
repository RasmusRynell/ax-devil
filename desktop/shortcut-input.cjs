// Smoke shortcuts use Qt portable names, just like the saved bindings.
function shortcutInput(chord, platform = process.platform) {
  if (!["darwin", "linux", "win32"].includes(platform))
    throw new Error(`Unsupported smoke shortcut platform: ${platform}`);
  const parts = chord.split("+");
  const keyCode = chord.endsWith("+") ? "+" : parts.pop();
  if (keyCode === "+") parts.splice(-2);
  const names = {
    Ctrl: platform === "darwin" ? "meta" : "control",
    Meta: platform === "darwin" ? "control" : "meta",
    Alt: "alt",
    Shift: "shift",
  };
  return {
    keyCode,
    modifiers: parts.map((part) => {
      if (!names[part]) throw new Error(`Unknown smoke shortcut modifier: ${part}`);
      return names[part];
    }),
  };
}

// Mirrors kit/shortcut.ts: one native display for plain labels and key parts.
function shortcutDisplay(chord, platform = process.platform) {
  const { keyCode, modifiers } = shortcutInput(chord, platform);
  const mac = platform === "darwin";
  const names = mac
    ? { control: "⌃", alt: "⌥", shift: "⇧", meta: "⌘" }
    : { meta: "Meta", control: "Ctrl", alt: "Alt", shift: "Shift" };
  const held = mac ? Object.keys(names).filter((name) => modifiers.includes(name)) : modifiers;
  const parts = [...held.map((name) => names[name]), keyCode];
  return { parts, text: parts.join(mac ? "" : "+") };
}

function pressShortcut(window, chord, { keypad = false, character = false } = {}) {
  const input = shortcutInput(chord);
  if (keypad) input.modifiers.push("isKeypad");
  window.webContents.sendInputEvent({ type: "keyDown", ...input });
  const text = character && { Enter: "\r", Return: "\r", Space: " " }[input.keyCode];
  if (text) window.webContents.sendInputEvent({ type: "char", ...input, keyCode: text });
  window.webContents.sendInputEvent({ type: "keyUp", ...input });
}

module.exports = { shortcutInput, shortcutDisplay, pressShortcut };
