const path = require("node:path");
const assert = require("node:assert/strict");
const { SMOKE_ROOT, openAddVideo, page, setView } = require("./smoke-kit.cjs");
const { input, point, focus, withUnfocused } = require("./smoke-x11.cjs");
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
  const hover = () =>
    withUnfocused(window, async () => {
      await focus(window);
      await input("move", 1200, 950);
      const at = await point(window, ROW);
      await input("move", at.x, at.y);
      await wait(CARD);
    });
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
  await withUnfocused(window, async () => {
    await wait(`!${CARD}`);
    await focus(window);
    assert.equal(await js(`Boolean(${CARD})`), false, "reactivation does not revive the old hover");
  });
};
