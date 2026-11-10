// Native button activation, shortcut capture and modal focus restoration need Chromium's default actions.
const assert = require("node:assert/strict");
const { page, setView } = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
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
    press("n", ["control"]);
    await wait("document.querySelector('dialog[open] h2')?.textContent === 'Add Video'");
    await wait("document.activeElement?.closest('dialog')");
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
  const key = "document.querySelector('[aria-label=\"Key for Exit\"]')";
  await open();
  // Keypad Enter carries Chromium's keypad location; Qt incorrectly lets it accept the whole dialog (KD-03).
  for (const [activation, modifiers] of [
    ["Enter", []],
    ["Space", []],
    ["Enter", ["isKeypad"]],
  ]) {
    await js(`${key}.focus()`);
    await js(
      "document.addEventListener('keydown', event => { window.smokeKeyLocation = event.location; }, { once: true })",
    );
    press(activation, modifiers);
    await wait(`${key}?.textContent === 'Press a key…'`);
    assert.equal(await js("window.smokeKeyLocation"), modifiers.includes("isKeypad") ? 3 : 0);
    press("y", ["control", "alt"]);
    await wait(`${key}.textContent !== 'Press a key…'`);
    assert.equal(
      await js("document.querySelector('[role=status]')?.textContent"),
      "Exit set to Ctrl+Alt+Y",
    );
    assert.equal(await js(`${key}.textContent.includes('Press a key')`), false);
    assert.equal(await js(`document.activeElement === ${key}`), true);
  }
  await answer("OK");
  await open();
  assert.equal(await js(`${key}.textContent.includes('Y')`), true, "OK keeps the captured binding");
  // Space's release must not reactivate the button after capturing it; Escape cancels capture only.
  const play = "document.querySelector('[aria-label=\"Key for Play / Pause\"]')";
  await js(`${play}.focus()`);
  press("Space");
  await wait(`${play}.textContent === 'Press a key…'`);
  press("Space");
  await wait(
    "document.querySelector('[role=status]')?.textContent === 'Play / Pause set to Space'",
  );
  assert.equal(await js(`${play}.textContent.includes('Press a key')`), false);
  press("Enter");
  await wait(`${play}.textContent === 'Press a key…'`);
  press("Escape");
  await wait(`${play}.textContent !== 'Press a key…'`);
  assert.equal(await js("!!document.querySelector('dialog[open]')"), true);
  await js(
    "[...document.querySelectorAll('dialog button')].find(b => b.textContent === 'Reset to Defaults').click()",
  );
  await answer("OK");
};
