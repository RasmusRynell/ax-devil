// The smoke test's check of a pane's header menu on a video that is open.
const assert = require("node:assert/strict");
const { SCREENSHOTS, frames, setView } = require("./smoke-kit.cjs");
const fs = require("node:fs/promises");
const path = require("node:path");

const SIDEBAR = "document.querySelector('[aria-label=Workspace]').click()";
const SIZES = [
  [[1100, 720], 1, "", "1100x720"],
  [[640, 480], 2, "-min-200", "640x480 at 200% zoom"],
];

/**
 * A right click on the pane's header offers the row's menu without Edit, whole inside the window even at the smallest
 * size and 200% zoom (with the sidebar closed there, as the pane has room only then). Saved in `theme`; the window ends
 * at 1100x720 with the viewer focused, as the steps after it expect.
 */
module.exports.headerMenu = async (window, { js, wait, key, theme }) => {
  for (const [size, zoom, suffix, where] of SIZES) {
    await setView(window, size, zoom);
    if (zoom === 2) await js(SIDEBAR);
    await js(
      "(() => { const h = document.querySelector('[role=group] [draggable]'); const r = h.getBoundingClientRect(); h.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: Math.round(r.right - 8), clientY: Math.round(r.top + r.height / 2) })); })()",
    );
    await wait("document.querySelector('[role=menu] [role=menuitem]')");
    const header = await js(
      "(() => { const m = document.querySelector('[role=menu]').getBoundingClientRect(); return { names: [...document.querySelectorAll('[role=menu] [role=menuitem]')].map((i) => i.textContent), inside: m.left >= 0 && m.top >= 0 && m.right <= innerWidth && m.bottom <= innerHeight }; })()",
    );
    assert.deepEqual(
      header.names,
      ["Open", "Open to the Side", "Information", "Export video", "Remove"],
      "The header menu is the row's menu without Edit",
    );
    assert.equal(header.inside, true, `The header menu fits the ${String(where)} window`);
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, `video-header-menu-${theme}${String(suffix)}.png`),
      (await window.webContents.capturePage()).toPNG(),
    );
    await key("Escape");
    await wait("!document.querySelector('[role=menu]')");
    if (zoom === 2) await js(SIDEBAR);
  }
  await setView(window, [1100, 720], 1);
  // The menu took the focus from the viewer, as the pointer does.
  await js("document.querySelector('[data-chrome-idle]').closest('[tabindex]').focus()");
};
