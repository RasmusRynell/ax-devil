// Settings smoke actions use real input only after the target is visible, uncovered and stationary.
const { pressShortcut } = require("./shortcut-input.cjs");
const assert = require("node:assert/strict");
const { page, settled } = require("./smoke-kit.cjs");

function pointFor(expression) {
  return `(() => {
    const element = ${expression};
    if (!element || element.matches(':disabled, [aria-disabled=true]')) return null;
    const box = element.getBoundingClientRect();
    if (!box.width || !box.height || box.left < 0 || box.top < 0 || box.right > innerWidth || box.bottom > innerHeight) return null;
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      const clip = parent.getBoundingClientRect();
      if (/(auto|scroll|hidden|clip)/.test(style.overflowX) && (box.left < clip.left - 1 || box.right > clip.right + 1)) return null;
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY) && (box.top < clip.top - 1 || box.bottom > clip.bottom + 1)) return null;
      // A modal is in the top layer and escapes the clipping of its DOM ancestors.
      if (parent.matches("dialog[open]")) break;
    }
    const x = (box.left + box.right) / 2, y = (box.top + box.bottom) / 2;
    return element.contains(document.elementFromPoint(x, y)) ? { x, y } : null;
  })()`;
}

async function reachable(window, expression) {
  const { wait } = page(window);
  const point = pointFor(expression);
  await wait(`${point} !== null`);
  const result = await settled(window, point);
  assert.ok(result, `The control must remain reachable: ${expression}`);
  return result;
}

async function clickVisible(window, expression) {
  const point = await reachable(window, expression);
  const zoom = window.webContents.getZoomFactor();
  for (const type of ["mouseMove", "mouseDown", "mouseUp"])
    window.webContents.sendInputEvent({
      type,
      x: Math.round(point.x * zoom),
      y: Math.round(point.y * zoom),
      button: "left",
      clickCount: 1,
    });
}

async function openSettings(window) {
  pressShortcut(window, "Ctrl+,");
  await page(window).wait("document.querySelector('dialog[open] h2')?.textContent === 'Settings'");
}

const activeTab =
  "document.querySelector('[role=tablist][aria-label=\"Settings pages\"] [aria-selected=true]')";

async function focusedSettingsPage(window, name) {
  const { wait } = page(window);
  await wait(
    `document.activeElement === ${activeTab} && ${activeTab}?.textContent === ${JSON.stringify(name)} && document.querySelector('[role=tabpanel]')?.ariaLabel === ${JSON.stringify(name)}`,
  );
  await reachable(window, activeTab);
}

async function settingsPage(window, name) {
  const { js, wait } = page(window);
  await clickVisible(window, activeTab);
  const names = await js(
    "[...document.querySelectorAll('[role=tablist][aria-label=\"Settings pages\"] [role=tab]')].map(b => b.textContent)",
  );
  assert.ok(names.includes(name), `Settings has page ${name}`);
  for (let steps = 0; await js(`${activeTab}.textContent !== ${JSON.stringify(name)}`); steps++) {
    assert.ok(steps < names.length, "ArrowRight must reach every settings page");
    const previous = await js(`${activeTab}.textContent`);
    pressShortcut(window, "Right");
    await wait(`${activeTab}.textContent !== ${JSON.stringify(previous)}`);
  }
  await focusedSettingsPage(window, name);
}

module.exports = { clickVisible, reachable, openSettings, settingsPage, focusedSettingsPage };
