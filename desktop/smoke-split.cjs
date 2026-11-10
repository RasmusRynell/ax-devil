// The smoke test's split-area scenario on the video that is open: Ctrl+double click on its row opens a second, pinned pane
// to the side; a header dragged over the first pane fills the half it would take and, dropped, moves the pane without
// restarting its picture or unpinning it; a seam dragged with the mouse resizes the two without collapsing either.
// The native drag loop does not run on a virtual display, so the drag is the page's own drag events. Screenshots in both
// themes, and the panes' fit at the window minimum and 200% zoom.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { headerFailure } = require("./smoke-header-failure.cjs");
const { SCREENSHOTS, delay, frames, moveTo, page, setView } = require("./smoke-kit.cjs");

const PANES = "[...document.querySelectorAll('[role=group][data-cursor-id^=\"pane:\"]')]";
const BOXES = `${PANES}.map((pane) => { const r = pane.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })`;

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const theme = nativeTheme.themeSource;
  const setTheme = async (shown) => {
    nativeTheme.themeSource = shown;
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
    await frames({ js });
  };
  const shoot = async (name) => {
    await fs.mkdir(SCREENSHOTS, { recursive: true });
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const both = async (name, check) => {
    for (const shown of ["light", "dark"]) {
      await setTheme(shown);
      await check?.();
      await shoot(`${name}-${shown}.png`);
    }
  };
  const sidebar = async (open) => {
    const shownNow = await js("Boolean(document.querySelector('aside[aria-label=Sidebar]'))");
    if (shownNow !== open) await js("document.querySelector('[aria-label=Workspace]').click()");
    await wait(`Boolean(document.querySelector('aside[aria-label=Sidebar]')) === ${open}`);
  };
  // The panes at the window minimum and 200% zoom, where the sidebar leaves the window to the panes.
  const small = async (name) => {
    await sidebar(false);
    // An odd size, so the area is a half pixel short of a whole one, which it must not scroll sideways for.
    await setView(window, [641, 481], 2);
    await both(name, async () => {
      assert.deepEqual(
        await js(`(${fit.split.toString()})()`),
        [],
        `${name} must fit at 200% zoom`,
      );
      assert.equal(
        await js(
          "(() => { const area = document.querySelector('main > div'); return area.scrollWidth > area.clientWidth; })()",
        ),
        false,
        `${name} must not scroll sideways`,
      );
    });
    await setView(window, [1100, 720], 1);
    await sidebar(true);
  };

  await setView(window, [1100, 720], 1);
  await sidebar(true);
  assert.equal(await js(`${PANES}.length`), 1, "One viewer is open");
  const row =
    "[...document.querySelectorAll('[role=treeitem]')].find((r) => r.textContent.startsWith('long_gop_h264.mp4'))";
  await wait(row);

  // Open to the Side: a second pane, pinned, to the right of the first, which stays a preview.
  await js(`${row}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, ctrlKey: true }))`);
  await wait(`${PANES}.length === 2`);
  const pins = () =>
    js(`${PANES}.map((pane) => pane.querySelector('[draggable] button').ariaPressed)`);
  assert.deepEqual(await pins(), ["false", "true"]);
  const [first, second] = await js(BOXES);
  assert.ok(
    second.left >= first.left + first.width - 1,
    "The new pane is to the right of the first",
  );
  assert.ok(Math.abs(second.width - first.width) <= 2, "A new split starts equal");
  await wait(`${PANES}[1].querySelector('canvas')`);
  await both("split");
  await small("split-small");

  // A header dragged over the first pane's top band fills its upper half.
  const dragOver = (fx, fy) =>
    js(`(() => {
      const [from, target] = ${PANES}.reverse();
      const data = new DataTransfer();
      window.smokeDrag = { data, from, target, canvas: from.querySelector('canvas') };
      const box = target.getBoundingClientRect();
      const at = { bubbles: true, cancelable: true, dataTransfer: data, clientX: box.left + box.width * ${fx}, clientY: box.top + box.height * ${fy} };
      from.querySelector('[draggable]').dispatchEvent(new DragEvent('dragstart', at));
      target.dispatchEvent(new DragEvent('dragenter', at));
      target.dispatchEvent(new DragEvent('dragover', at));
    })()`);
  const hint = () =>
    js(`(() => {
      const { target } = window.smokeDrag;
      const shown = target.querySelector(':scope > .pointer-events-none');
      if (!shown) return null;
      const [box, over] = [target.getBoundingClientRect(), shown.getBoundingClientRect()];
      return { top: over.top - box.top, width: over.width - box.width, height: over.height / box.height };
    })()`);
  await dragOver(0.5, 0.1);
  const half = await hint();
  assert.ok(half && Math.abs(half.top) <= 1 && Math.abs(half.width) <= 2, "The hint fills the top");
  assert.ok(Math.abs(half.height - 0.5) < 0.02, "The hint is half the pane");
  await both("split-drop-hint");
  // Escape or leaving cancels the drag: the hint goes and nothing moved.
  await js(`(() => {
    const { from, target, data } = window.smokeDrag;
    target.dispatchEvent(new DragEvent('dragleave', { bubbles: true, dataTransfer: data }));
    from.querySelector('[draggable]').dispatchEvent(new DragEvent('dragend', { bubbles: true }));
  })()`);
  assert.equal(await hint(), null, "The hint is gone once the drag is over");
  assert.deepEqual(
    await js(BOXES),
    [first, second].map(({ left, top, width, height }) => ({ left, top, width, height })),
  );

  // Dropped on the top band, the pinned pane moves above the first: stacked, still pinned, the same picture.
  await dragOver(0.5, 0.1);
  await js(`(() => {
    const { from, target, data } = window.smokeDrag;
    const box = target.getBoundingClientRect();
    const at = { bubbles: true, cancelable: true, dataTransfer: data, clientX: box.left + box.width / 2, clientY: box.top + box.height * 0.1 };
    target.dispatchEvent(new DragEvent('drop', at));
    from.querySelector('[draggable]').dispatchEvent(new DragEvent('dragend', { bubbles: true }));
  })()`);
  await wait(`${PANES}[1].getBoundingClientRect().top < ${PANES}[0].getBoundingClientRect().top`);
  const [below, above] = await js(BOXES);
  assert.ok(above.top + above.height <= below.top + 1, "The moved pane is above the other");
  assert.ok(Math.abs(above.left - below.left) <= 1 && Math.abs(above.width - below.width) <= 1);
  assert.deepEqual(await pins(), ["false", "true"], "Moving keeps the pin");
  assert.equal(
    await js(`window.smokeDrag.canvas === ${PANES}[1].querySelector('canvas')`),
    true,
    "Moving does not restart the picture",
  );
  await both("split-stacked");
  await small("split-stacked-small");

  // The seam between them follows the mouse and stops short of collapsing either pane.
  const seam = "document.querySelector('[role=separator][aria-label=\"Resize panes\"]')";
  const where = () =>
    js(
      `(() => { const r = ${seam}.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`,
    );
  const press = (type, x, y, modifiers = []) =>
    window.webContents.sendInputEvent({
      type,
      x: Math.round(x),
      y: Math.round(y),
      button: "left",
      clickCount: 1,
      modifiers,
    });
  const drag = async (dy) => {
    const { x, y } = await where();
    await moveTo(window, x, y);
    press("mouseDown", x, y);
    for (let step = 1; step <= 6; step++)
      press("mouseMove", x, y + (dy * step) / 6, ["leftButtonDown"]);
    press("mouseUp", x, y + dy);
    await delay(300);
    await frames({ js });
  };
  // The panes follow the area a frame behind a resize, and slower where frames are slow: read them once two reads
  // 100 ms apart agree.
  const settled = async () => {
    let was = JSON.stringify(await js(BOXES));
    for (let attempt = 0; attempt < 30; attempt++) {
      await delay(100);
      const now = JSON.stringify(await js(BOXES));
      if (now === was) return JSON.parse(now);
      was = now;
    }
    throw new Error("The panes did not settle");
  };
  const before = (await settled())[1].height;
  await drag(60);
  const grown = (await settled())[1].height;
  assert.ok(
    Math.abs(grown - before - 60) <= 3,
    `The seam moves with the mouse: ${before} to ${grown}`,
  );
  await drag(2000);
  const [low, high] = (await js(BOXES)).map(({ height }) => height);
  assert.ok(low > 100 && high > 100, `Neither pane collapses: ${low} and ${high}`);
  await shoot("split-resized.png");

  // More panes than the smallest window holds at 200% scroll instead of squeezing: four panes in a row, the area
  // wider than the window, and the last pane's buttons within reach once it is scrolled to its end.
  for (const count of [3, 4]) {
    await js(`${row}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, ctrlKey: true }))`);
    await wait(`${PANES}.length === ${count}`);
  }
  await sidebar(false);
  await setView(window, [640, 480], 2);
  const area = "document.querySelector('main > div')";
  assert.equal(await js(`${area}.scrollWidth > ${area}.clientWidth`), true, "The area scrolls");
  await both("split-many-small", async () => {
    for (const end of [0, 1e6]) {
      await js(`${area}.scrollTo(${end}, 0)`);
      await frames({ js });
      assert.deepEqual(await js(`(${fit.split.toString()})()`), [], "Four panes fit at 200%");
    }
    await js(`${area}.scrollTo(0, 0)`);
  });
  await setView(window, [1100, 720], 1);
  await sidebar(true);

  // A window holds eight panes: Open to the Side from a header with all taken fails, and the header says so in its own
  // pane, above the viewer, until dismissed.
  for (let count = 5; count <= 8; count++) {
    await js(`${row}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, ctrlKey: true }))`);
    await wait(`${PANES}.length === ${count}`);
  }
  await headerFailure(window, { js, wait, both, sidebar, panes: PANES });

  // Closing the panes but the first leaves it as the one open viewer, for the scenarios that follow.
  for (let open = 8; open > 1; open--) {
    await js(`${PANES}[${open - 1}].querySelector('[aria-label="Close this pane"]').click()`);
    await wait(`${PANES}.length === ${open - 1}`);
  }
  assert.deepEqual(await pins(), ["false"]);
  nativeTheme.themeSource = theme;
};
