// The smoke test's sidebar scenario: same-named videos get a detail line the search matches, the sidebar's edge drags
// within its limits, the width survives hiding and showing, and a drag to nothing hides the sidebar. Screenshots in both
// themes at normal zoom and at 200%.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { SCREENSHOTS, SMOKE_ROOT, frames, moveTo, page, setView } = require("./smoke-kit.cjs");

const FIXTURE = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
const DIVIDER = "document.querySelector('[role=separator][aria-label=\"Resize sidebar\"]')";
const SIDEBAR = "document.querySelector('aside[aria-label=Sidebar]')";

module.exports.sidebar = async (window) => {
  const { js, wait } = page(window);
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const dialogButton = (label) =>
    `[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === ${JSON.stringify(label)})`;
  const setValue = (selector, text) =>
    js(`(() => {
      const input = ${selector};
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(text)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
  const rows = "[...document.querySelectorAll('[aria-label=Sidebar] [role=treeitem]')]";
  const clips = `${rows}.filter((r) => r.textContent.startsWith('clip.mp4'))`;
  const width = () => js(`${SIDEBAR}?.getBoundingClientRect().width ?? 0`);
  // A real drag of the divider by `dx` CSS pixels.
  const drag = async (dx) => {
    const { x, y } = await js(
      `(() => { const r = ${DIVIDER}.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`,
    );
    await moveTo(window, x, y);
    const zoom = window.webContents.getZoomFactor();
    const at = (px) => ({ x: Math.round(px * zoom), y: Math.round(y * zoom) });
    window.webContents.sendInputEvent({
      type: "mouseDown",
      ...at(x),
      button: "left",
      clickCount: 1,
    });
    for (const step of [0.25, 0.5, 0.75, 1])
      window.webContents.sendInputEvent({
        type: "mouseMove",
        ...at(x + dx * step),
        modifiers: ["leftButtonDown"],
      });
    window.webContents.sendInputEvent({
      type: "mouseUp",
      ...at(x + dx),
      button: "left",
      clickCount: 1,
    });
    await frames({ js });
  };

  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-sidebar-"));
  const theme = nativeTheme.themeSource;
  try {
    await fs.mkdir(SCREENSHOTS, { recursive: true });
    await setView(window, [1200, 800], 1);
    await js(
      "!document.querySelector('[aria-label=\"Workspace items\"]') && document.querySelector('[aria-label=Workspace]').click()",
    );
    for (const site of ["north", "south"]) {
      await fs.mkdir(path.join(folder, site));
      await fs.copyFile(FIXTURE, path.join(folder, site, "clip.mp4"));
      await wait("document.querySelector('[data-cursor-id=\"sidebar:add-video\"]')");
      await js("document.querySelector('[data-cursor-id=\"sidebar:add-video\"]').click()");
      await wait("document.querySelector('dialog[open] input')");
      await setValue(
        "document.querySelector('dialog[open] input')",
        path.join(folder, site, "clip.mp4"),
      );
      await wait(`${dialogButton("Add")}?.disabled === false`);
      await js(`${dialogButton("Add")}.click()`);
      await wait(
        `!document.querySelector('dialog') && ${clips}.length === ${site === "north" ? 1 : 2}`,
      );
    }
    // Both rows are called clip.mp4, and each says which folder it is in.
    await wait(
      `${clips}.filter((r) => /^clip\\.mp4, (north|south)$/.test(r.textContent)).length === 2`,
    );
    await setValue("document.querySelector('[aria-label=Sidebar] input[type=search]')", "SOUTH");
    await wait(`${rows}.length === 1 && ${rows}[0].textContent.endsWith('south')`);
    await setValue("document.querySelector('[aria-label=Sidebar] input[type=search]')", "");
    await wait(`${clips}.length === 2`);

    // The edge drags between what the view needs and half the space beside the activity bar.
    const start = await width();
    await drag(120);
    const wider = await width();
    assert.ok(Math.abs(wider - (start + 120)) <= 2, `dragged 120 wider: ${start} to ${wider}`);
    await drag(2000);
    const cap = await js(
      `(innerWidth - document.querySelector('nav[aria-label=Activity]').clientWidth) / 2`,
    );
    assert.ok(
      Math.abs((await width()) - cap) <= 2,
      `the cap is half the space: ${await width()} of ${cap}`,
    );
    await drag(400 - cap);
    const kept = await width();
    assert.ok(kept < cap - 100 && kept > start - 200, `a drag back narrows it: ${kept}`);
    // Hiding and showing the sidebar keeps the width.
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await wait(`!${SIDEBAR}`);
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await wait(SIDEBAR);
    assert.equal(await width(), kept, "The width survives hiding and showing");
    // A drag to nothing hides it on release.
    await drag(-2000);
    await wait(`!${SIDEBAR}`);
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await wait(SIDEBAR);
    await drag(wider - (await width()));

    for (const { zoom, view, suffix } of [
      { zoom: 1, view: [1200, 800], suffix: "" },
      { zoom: 2, view: [1500, 1500], suffix: "-200" },
    ]) {
      await setView(window, view, zoom);
      for (const shown of ["light", "dark"]) {
        nativeTheme.themeSource = shown;
        await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
        await shoot(`sidebar-resized-${shown}${suffix}.png`);
        // Nothing in the sidebar is cut off, and it stays within its half of the window. A tall list scrolls, as it
        // grows with the user's data, and the viewer's tabs scroll too: neither is this scenario's to check.
        assert.deepEqual(
          await js(`(() => {
            const aside = ${SIDEBAR};
            const cut = [...aside.querySelectorAll('*')].filter(
              (e) => e.localName !== 'input' && e.clientWidth > 2 && e.scrollWidth > e.clientWidth + 1,
            );
            const strip = document.querySelector('nav[aria-label=Activity]').clientWidth;
            const over = aside.getBoundingClientRect().width > (innerWidth - strip) / 2 + 1;
            return [...cut.map((e) => e.textContent.trim().slice(0, 30)), ...(over ? ['past half the window'] : [])];
          })()`),
          [],
          `The sidebar fits at ${zoom * 100}% zoom`,
        );
      }
    }
    // The items go, leaving the workspace as it was.
    await setView(window, [1200, 800], 1);
    for (let left = 2; left > 0; left--) {
      // The tree keeps the row it is on from the click, which it must have drawn before the key.
      await js(`${clips}[0].click()`);
      await frames({ js });
      await js(
        `${clips}[0].closest('[role=tree]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }))`,
      );
      await wait(`${clips}.length === ${left - 1}`);
    }
  } finally {
    nativeTheme.themeSource = theme;
    await fs.rm(folder, { recursive: true, force: true });
  }
};
