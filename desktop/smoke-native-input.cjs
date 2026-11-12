// Native button activation, shortcut capture and modal focus restoration need Chromium's default actions.
const assert = require("node:assert/strict");
const { nativeTheme } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, frames, page, setView } = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const control = process.platform === "darwin" ? "meta" : "control";
  const controlChip = process.platform === "darwin" ? "⌘" : "Ctrl";
  const altChip = process.platform === "darwin" ? "⌥" : "Alt";
  await setView(window, [1100, 720], 1);
  const press = (keyCode, modifiers = []) => {
    window.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers });
    if (keyCode === "Enter" || keyCode === "Space")
      window.webContents.sendInputEvent({
        type: "char",
        keyCode: keyCode === "Enter" ? "\r" : " ",
        modifiers,
      });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers });
  };
  const answer = async (label) => {
    await js(
      `[...document.querySelectorAll('dialog button')].find(b => b.textContent === '${label}').click()`,
    );
    await wait("!document.querySelector('dialog[open]')");
  };

  // The keyboard shortcut opens over the user's current control. Both dismissal routes must give it back.
  const opener = "document.querySelector('[aria-label=Settings]')";
  for (const close of ["Cancel", "Escape"]) {
    await js(`${opener}.focus()`);
    press("n", [control]);
    await wait("document.querySelector('dialog[open] h2')?.textContent === 'Add Video'");
    await wait("document.activeElement?.closest('dialog')");
    assert.equal(await js("document.querySelector('dialog[open]').matches(':modal')"), true);
    if (close === "Cancel") await answer(close);
    else {
      press("Escape");
      await wait("!document.querySelector('dialog[open]')");
    }
    assert.equal(
      await js(`document.activeElement === ${opener}`),
      true,
      `${close} restores the pre-dialog focus`,
    );
  }

  const open = async () => {
    await js(
      "[...document.querySelectorAll('[data-top]')].find(b => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find(b => b.textContent.startsWith('Keyboard Shortcuts')).click()",
    );
    await wait("document.activeElement?.getAttribute('aria-label') === 'Search shortcuts'");
  };
  const binding = (button) =>
    js(`[...${button}.querySelectorAll('kbd')].map(chip => chip.textContent)`);
  const key = "document.querySelector('[aria-label=\"Key for Exit\"]')";
  await open();
  // Keypad Enter carries Chromium's keypad location; Qt incorrectly lets it accept the whole dialog (KD-03).
  for (const [activation, modifiers, captured] of [
    ["Enter", [], "y"],
    ["Space", [], "u"],
    ["Enter", ["isKeypad"], "j"],
    ["F2", [], "j"],
  ]) {
    await js(`${key}.focus()`);
    await js(
      "document.addEventListener('keydown', event => { window.smokeKeyLocation = event.location; }, { once: true, capture: true })",
    );
    press(activation, modifiers);
    await wait(`${key}?.textContent === 'Press a key…'`);
    assert.equal(await js("window.smokeKeyLocation"), modifiers.includes("isKeypad") ? 3 : 0);
    press(captured, [control, "alt"]);
    await wait(`${key}.textContent !== 'Press a key…'`);
    assert.equal(
      await js("document.querySelector('[role=status]')?.textContent"),
      `Exit set to Ctrl+Alt+${captured.toUpperCase()}`,
    );
    assert.deepEqual(
      await binding(key),
      process.platform === "darwin"
        ? [altChip, controlChip, captured.toUpperCase()]
        : [controlChip, altChip, captured.toUpperCase()],
    );
    assert.equal(await js(`document.activeElement === ${key}`), true);
  }
  await answer("OK");
  await open();
  assert.deepEqual(
    await binding(key),
    process.platform === "darwin" ? [altChip, controlChip, "J"] : [controlChip, altChip, "J"],
    "OK keeps the complete binding",
  );
  // Adding Meta to reserved Ctrl+0 makes a distinct chord: capture, persist and dispatch it without page zoom.
  const settings = "document.querySelector('[aria-label=\"Key for Settings\"]')";
  await js(`${settings}.focus()`);
  press("F2");
  await wait(`${settings}.textContent === 'Press a key…'`);
  const meta = process.platform === "darwin" ? "control" : "meta";
  press("0", [meta, control]);
  await wait(`${settings}.textContent !== 'Press a key…'`);
  assert.deepEqual(
    await binding(settings),
    process.platform === "darwin" ? ["⌃", "⌘", "0"] : ["Meta", "Ctrl", "0"],
  );
  const originalTheme = nativeTheme.themeSource;
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    for (const zoom of [1, 2]) {
      await setView(window, [640, 480], zoom);
      await js(`${settings}.scrollIntoView({ block: 'center' })`);
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `Meta chips fit at ${zoom * 100}%`,
      );
      await frames({ js });
      await fs.writeFile(
        path.join(SCREENSHOTS, `shortcuts-meta-${theme}-${zoom * 100}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
    }
  }
  nativeTheme.themeSource = originalTheme;
  await setView(window, [1100, 720], 1);
  await answer("OK");
  press("0", [meta, control]);
  await wait("document.querySelector('dialog[open] h2')?.textContent === 'Settings'");
  await answer("Cancel");
  await open();
  assert.deepEqual(
    await binding(settings),
    process.platform === "darwin" ? ["⌃", "⌘", "0"] : ["Meta", "Ctrl", "0"],
  );
  // Space's release must not reactivate the button after capturing it; Escape cancels capture only.
  const play = "document.querySelector('[aria-label=\"Key for Play / Pause\"]')";
  await js("document.querySelector('[aria-label=\"Clear Play / Pause\"]').click()");
  await wait(`${play}.textContent === 'Not set'`);
  await js(`${play}.focus()`);
  press("Space");
  await wait(`${play}.textContent === 'Press a key…'`);
  press("Space");
  await wait(
    "document.querySelector('[role=status]')?.textContent === 'Play / Pause set to Space'",
  );
  assert.deepEqual(await binding(play), ["Space"]);
  press("Enter");
  await wait(`${play}.textContent === 'Press a key…'`);
  press("Escape");
  await wait(`${play}.textContent !== 'Press a key…'`);
  assert.equal(await js("!!document.querySelector('dialog[open]')"), true);
  assert.deepEqual(await binding(play), ["Space"], "Escape keeps the previous binding");
  await js(
    "[...document.querySelectorAll('dialog button')].find(b => b.textContent === 'Reset to Defaults').click()",
  );
  await answer("OK");
};
