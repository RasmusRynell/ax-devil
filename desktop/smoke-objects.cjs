// The smoke test's media tools: the Objects list of a lane of MINI-01 (lanes DPM, GT, FRCNN, boxes on frame 0 and none on
// frame 5), opened with its shortcut. Frame scope lists the frame's objects, File scope the file's with the ones that are
// not in the frame dimmed, a click on one of those goes to its first frame and inspects it. Shown in both themes, then at
// the smallest window at 200% zoom, where the list must fit.
const assert = require("node:assert/strict");
const fit = require("./fit.cjs");
const { delay, goToFrame, page, setView } = require("./smoke-kit.cjs");

const PANEL = 'aside[aria-label="Media tools"]';
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
      dimmed: row.title === 'Not in this frame',
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
  const fits = async (what) =>
    assert.deepEqual(await js(`(${fit.panel.toString()})(${JSON.stringify(PANEL)})`), [], what);

  await setView(window, [1100, 720], 1);
  await goToFrame(window, 0);
  key("B", ["control"]);
  await wait(`document.querySelector('${PANEL}')`);

  // Frame scope: what the lane draws on frame 0.
  await wait(`document.querySelectorAll('${PANEL} [role=option]').length > 0`);
  const inFrame = await rows();
  assert.ok(
    inFrame.every((row) => !row.dimmed),
    "Frame scope dims nothing",
  );
  assert.equal((await count())[0], inFrame.length);
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-frame-${theme}.png`);
  }

  // File scope: every object of the file; the ones of the frame are not dimmed. Searching narrows it.
  await scope("File");
  await wait(`document.querySelectorAll('${PANEL} [role=option]:not([aria-busy])').length > 0`);
  const inFile = await rows();
  const [matched, total] = await count();
  assert.equal(matched, total);
  assert.ok(total > inFrame.length, "The file has objects the frame has not");
  assert.deepEqual(
    inFile
      .filter((row) => !row.dimmed)
      .map((row) => row.id)
      .sort(),
    inFrame.map((row) => row.id).sort(),
    "File scope dims exactly the objects that are not in the frame",
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
  await js(
    `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('${PANEL} input:not([type=radio])'), '')`,
  );
  await js(
    `document.querySelector('${PANEL} input:not([type=radio])').dispatchEvent(new Event('input', { bubbles: true }))`,
  );
  await shows(total, total);

  // On frame 5 nothing is in the frame. A click on an object goes to the frame it first is on and inspects it. The window
  // is wide enough for the inspector to take a column beside the list.
  await setView(window, [1500, 720], 1);
  await goToFrame(window, 5);
  await wait(
    `(() => { const rows = [...document.querySelectorAll('${PANEL} [role=option]')]; return rows.length > 0 && rows.every((row) => row.title); })()`,
  );
  await js(`document.querySelector('${PANEL} [role=option]').click()`);
  await wait(
    "document.querySelector('aside[aria-label=Inspector] [aria-busy=false]') && !document.querySelector('aside[aria-label=Inspector]').innerText.includes('Not in this frame')",
  );
  assert.ok(
    (await rows()).some((row) => row.selected && !row.dimmed),
    "The object is selected and now in the frame",
  );
  // A pinned field rises into a section of its own.
  const INSPECTOR = "aside[aria-label=Inspector]";
  await js(`document.querySelector('${INSPECTOR} [data-act=pin][aria-label^=Pin]').click()`);
  await wait(`document.querySelector('${INSPECTOR} section[aria-label=Pinned]')`);
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`objects-inspected-${theme}.png`);
  }
  await js(`document.querySelector('${INSPECTOR} [data-act=pin][aria-label^=Unpin]').click()`);
  await js("document.querySelector('[aria-label=\"Close inspector\"]').click()");

  // The smallest window at 200%: the panel covers the viewer and nothing sticks out or is cut off.
  await setView(window, [640, 480], 2);
  await js("document.querySelector('[aria-label=Workspace]').click()");
  await wait("document.querySelector('aside h2')?.textContent !== 'Workspace'");
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
