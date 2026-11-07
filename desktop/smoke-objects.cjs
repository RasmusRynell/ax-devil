// The smoke test's media tools: the Objects list of a lane of MINI-01 (lanes DPM, GT, FRCNN, boxes on frame 0 and none on
// frame 5), opened with its shortcut. Frame scope lists the frame's objects, File scope the file's, where a click on an
// object that is not in the frame goes to its first frame and opens its page over the list. Shown in both themes, then at
// the smallest window at 200% zoom, where the list must fit.
const assert = require("node:assert/strict");
const fit = require("./fit.cjs");
const { delay, goToFrame, page, setView } = require("./smoke-kit.cjs");

const PANEL = 'aside[aria-label="Media tools"]';
const PAGE = `${PANEL} section[aria-label="Object page"]`;
const THEMES = ["light", "dark"];

module.exports.objects = async (window, { setTheme, shoot }) => {
  const { js, wait } = page(window);
  const key = (keyCode, modifiers = []) =>
    ["keyDown", "keyUp"].forEach((type) =>
      window.webContents.sendInputEvent({ type, keyCode, modifiers }),
    );
  const rows = () =>
    js(`[...document.querySelectorAll('${PANEL} [role=option]')].map((row) => ({
      id: row.firstElementChild.textContent,
      selected: row.getAttribute('aria-selected') === 'true',
    }))`);
  const count = () =>
    js(
      `document.querySelector('${PANEL}').innerText.match(/(\\d+) of (\\d+)/)?.slice(1).map(Number)`,
    );
  const shows = (matched, total) =>
    wait(`/\\b${matched} of ${total}\\b/.test(document.querySelector('${PANEL}').innerText)`);
  const scope = (name) =>
    js(
      `[...document.querySelectorAll('${PANEL} input[type=radio]')].find((r) => r.parentElement.textContent === '${name}').click()`,
    );
  // Empties the search box the way React sees an edit, with no shortcut: Ctrl+A is not select-all on macOS.
  const clearSearch = async () => {
    const input = `document.querySelector('${PANEL} input:not([type=radio])')`;
    await js(
      `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(${input}, '')`,
    );
    await js(`${input}.dispatchEvent(new Event('input', { bubbles: true }))`);
  };
  const fits = async (what) =>
    assert.deepEqual(await js(`(${fit.panel.toString()})(${JSON.stringify(PANEL)})`), [], what);

  await setView(window, [1100, 720], 1);
  await goToFrame(window, 0);
  key("B", ["control"]);
  await wait(`document.querySelector('${PANEL}')`);

  // Frame scope: what the lane draws on frame 0.
  await wait(`document.querySelectorAll('${PANEL} [role=option]').length > 0`);
  const inFrame = await rows();
  assert.equal((await count())[0], inFrame.length);
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-frame-${theme}.png`);
  }

  // A searched motion state no object in the frame has is named under the search box.
  await js(`document.querySelector('${PANEL} input:not([type=radio])').focus()`);
  window.webContents.insertText("state:moving");
  const noted = (text) =>
    `[...document.querySelectorAll('${PANEL} [role=status]')].some((note) => note.textContent === '${text}')`;
  await wait(noted("No object has state in this frame"));
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-missing-${theme}.png`);
  }
  await clearSearch();
  await wait(`!(${noted("No object has state in this frame")})`);

  // A field only the inspection has is searched through the backend, which reads it from the sample.
  await js(`document.querySelector('${PANEL} input:not([type=radio])').focus()`);
  window.webContents.insertText("visibility_ratio>0");
  await wait(
    `document.querySelector('${PANEL}').innerText.match(/(\\d+) of (\\d+)/)?.[1] > 0 && !document.querySelector('${PANEL} [aria-busy=true]')`,
  );
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-search-${theme}.png`);
  }
  await clearSearch();
  await shows(inFrame.length, inFrame.length);

  // Dragging the divider on the panel's left edge left widens it, and it keeps that width through a close.
  const width = () => js(`document.querySelector('${PANEL}').getBoundingClientRect().width`);
  const grip = await js(`(() => {
    const box = document.querySelector('${PANEL} [role=separator]').getBoundingClientRect();
    return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2) };
  })()`);
  const initial = await width();
  const mouse = (type, x, extra = {}) =>
    window.webContents.sendInputEvent({
      type,
      x,
      y: grip.y,
      button: "left",
      clickCount: 1,
      ...extra,
    });
  mouse("mouseDown", grip.x);
  for (const step of [20, 60, 100])
    mouse("mouseMove", grip.x - step, { modifiers: ["leftButtonDown"] });
  mouse("mouseUp", grip.x - 100);
  await wait(`document.querySelector('${PANEL}').getBoundingClientRect().width > ${initial + 50}`);
  const widened = await width();
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-resized-${theme}.png`);
  }
  key("B", ["control"]);
  await wait(`!document.querySelector('${PANEL}')`);
  key("B", ["control"]);
  await wait(`document.querySelector('${PANEL}')`);
  assert.equal(await width(), widened, "The panel opens at the width it was given");

  // File scope: every object of the file, the frame's among them. Searching narrows it.
  await scope("File");
  await wait(`document.querySelectorAll('${PANEL} [role=option]:not([aria-busy])').length > 0`);
  const inFile = await rows();
  const [matched, total] = await count();
  assert.equal(matched, total);
  assert.ok(total > inFrame.length, "The file has objects the frame has not");
  const inFileIds = new Set(inFile.map((row) => row.id));
  assert.ok(
    inFrame.every((row) => inFileIds.has(row.id)),
    "File scope lists the objects of the frame",
  );
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-file-${theme}.png`);
  }
  await js(`document.querySelector('${PANEL} input:not([type=radio])').focus()`);
  window.webContents.insertText(`id:${inFile[0].id} OR class:nothing`);
  await shows(1, total);
  assert.deepEqual(
    (await rows()).map((row) => row.id),
    [inFile[0].id],
  );
  await clearSearch();
  await shows(total, total);

  // On frame 5 nothing is in the frame. A click on an object goes to the frame it first is on and opens its page.
  await setView(window, [1500, 720], 1);
  await goToFrame(window, 5);
  await wait(`document.querySelector('${PANEL} [role=option]:not([aria-busy])')`);
  await js(`document.querySelector('${PANEL} [role=option]').click()`);
  await wait(
    `document.querySelector('${PAGE} [aria-busy=false]') && !document.querySelector('${PAGE}').innerText.includes('Not in this frame')`,
  );
  assert.ok(
    (await rows()).some((row) => row.selected),
    "The object is selected",
  );
  // Where the object appears is drawn under the header, once the backend has answered.
  await wait(`document.querySelector('${PAGE} [role=img][aria-label=Presence]')`);
  // A pinned field rises into a section of its own.
  await js(`document.querySelector('${PAGE} [data-act=pin][aria-label^=Pin]').click()`);
  await wait(`document.querySelector('${PAGE} section[aria-label=Pinned]')`);
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-inspected-${theme}.png`);
  }
  await js(`document.querySelector('${PAGE} [data-act=pin][aria-label^=Unpin]').click()`);
  // Back shows the list again, with the row the page came from marked.
  await js(`document.querySelector('${PAGE} button[aria-label^=Back]').click()`);
  await wait(`!document.querySelector('${PAGE}')`);
  assert.ok(
    (await rows()).some((row) => row.selected),
    "Back marks the row of the page it left",
  );

  // The smallest window at 200%: the panel fits and nothing sticks out or is cut off.
  await setView(window, [640, 480], 2);
  await js("document.querySelector('[aria-label=Workspace]').click()");
  await wait("!document.querySelector('[aria-label=\"Workspace items\"]')");
  for (const theme of THEMES) {
    await setTheme(theme);
    await delay(100);
    await fits(`The media tools must fit the 640x480 window at 200% zoom, ${theme}`);
    await shoot(`objects-${theme}-200.png`);
  }
  await setView(window, [1100, 720], 1);
  await setTheme("light");
  await js("document.querySelector('[aria-label=Workspace]').click()");
  await js(`document.querySelector('${PANEL} [aria-label="Close media tools"]').click()`);
  await wait(`!document.querySelector('${PANEL}')`);
};
