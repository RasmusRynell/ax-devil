const { spawn } = require("node:child_process");
const path = require("node:path");
const assert = require("node:assert/strict");
const { SMOKE_ROOT, openAddVideo, page, setView, until } = require("./smoke-kit.cjs");
const { input, point } = require("./smoke-x11.cjs");
const ROW = "document.querySelector('[role=treeitem]')";
const CARD = "document.querySelector('[aria-hidden] > canvas')";

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  await setView(window, [900, 700], 1);
  window.setPosition(30, 30);
  await openAddVideo(window);
  const fixture = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
  await js(`(() => {
    const field = document.querySelector('dialog input');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, ${JSON.stringify(fixture)});
    field.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  const add = "[...document.querySelectorAll('dialog button')].find(b => b.textContent === 'Add')";
  await wait(`${add}?.disabled === false`);
  await js(`${add}.click()`);
  await wait(`!document.querySelector('dialog') && ${ROW}`);
  const hover = async () => {
    await input("move", 1200, 950);
    const at = await point(window, ROW);
    await input("move", at.x, at.y);
    await wait(CARD);
  };
  // Switching away from the browser and hiding its tree must dispose the floating card even without pointerleave.
  for (const action of [
    "document.querySelector('[aria-label=Plugins]')",
    "document.querySelector('[aria-label=Workspace]')",
  ]) {
    await hover();
    await js(`${action}.click()`);
    await wait(`!${CARD}`);
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await wait(ROW);
    assert.equal(
      await js(`Boolean(${CARD})`),
      false,
      "showing the tree does not revive its old hover",
    );
  }
  await hover();
  assert.ok(window.isFocused(), "the hovered app starts active");
  const other = spawn("python3", [path.join(SMOKE_ROOT, "scripts/private-x11.py"), "competitor"], {
    stdio: ["pipe", "inherit", "inherit"],
  });
  const closed = new Promise((resolve, reject) => {
    other.once("error", reject);
    other.once("exit", resolve);
  });
  try {
    await until(() => !window.isFocused(), "native activation transfer to another X client");
    await wait(`!${CARD}`);
    window.focus();
    await until(() => window.isFocused(), "return to the app");
    assert.equal(await js(`Boolean(${CARD})`), false, "reactivation does not revive the old hover");
  } finally {
    other.stdin.end();
    await closed;
  }
};
