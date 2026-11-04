// The smoke test's panels over the viewer, at the smallest window and 200% text size, in both themes: the inspector of a
// selected object and a lane's settings menu in each of its tabs. Each must fit without anything clipped or sticking
// out, and a screenshot of each lands in the screenshots folder.
const assert = require("node:assert/strict");
const fit = require("./fit.cjs");
const { delay, moveTo, page, setView } = require("./smoke-kit.cjs");

const THEMES = ["light", "dark"];

module.exports.panels = async (window, { setTheme, shoot, showEntry, point }) => {
  const { js, wait } = page(window);
  const click = (type, [x, y], clickCount = 1) =>
    window.webContents.sendInputEvent({
      type,
      x: Math.round(x * window.webContents.getZoomFactor()),
      y: Math.round(y * window.webContents.getZoomFactor()),
      button: "left",
      clickCount,
    });
  const fits = async (selector, what) =>
    assert.deepEqual(await js(`(${fit.panel.toString()})(${JSON.stringify(selector)})`), [], what);

  // The picture stays zoomed on the object while it is chosen and while the interface is at 200%.
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`zoomed-${theme}.png`);
  }
  await moveTo(window, ...point);
  for (const type of ["mouseDown", "mouseUp"]) click(type, point);
  await wait("document.querySelector('aside[aria-label=Inspector] [aria-busy=false]')");
  await setView(window, [640, 480], 2);
  for (const theme of THEMES) {
    await setTheme(theme);
    await fits(
      "aside[aria-label=Inspector]",
      `The inspector must fit the 640x480 window at 200% zoom, ${theme}`,
    );
    await shoot(`inspector-${theme}-200.png`);
  }
  // At this size the inspector covers the viewer, with most of its height for the fields.
  const inspector = await js(`(() => {
    const aside = document.querySelector('aside[aria-label=Inspector]').getBoundingClientRect();
    const pane = document.querySelector('[data-popover-bounds]').getBoundingClientRect();
    const body = document.querySelector('aside[aria-label=Inspector] [aria-busy]').getBoundingClientRect();
    return { covers: aside.left <= pane.left && aside.right >= pane.right, share: body.height / aside.height };
  })()`);
  assert.ok(
    inspector.covers,
    "A narrow viewer's inspector must cover the viewer, not take a column",
  );
  assert.ok(
    inspector.share >= 0.65,
    `The inspector's fields must get most of its height: ${inspector.share}`,
  );
  await js("document.querySelector('[aria-label=\"Close inspector\"]').click()");
  await wait("!document.querySelector('aside[aria-label=Inspector]')");

  // Types a timeout into the open lane menu's sticky group and commits it, as the user does with Enter.
  const timeout = (ms) =>
    js(`(() => {
      const label = [...document.querySelectorAll('[role=group][aria-label=Sticky] label')].find(
        (l) => l.textContent === 'Timeout (ms)',
      );
      const input = document.getElementById(label.htmlFor);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '${ms}');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    })()`);

  // A double click on the picture shows it whole again. Then a lane of the entry with three lanes, which has every
  // button the menu can have.
  const middle = [window.getContentSize()[0] / 4, window.getContentSize()[1] / 4];
  await moveTo(window, ...middle);
  click("mouseDown", middle, 2);
  click("mouseUp", middle, 2);
  await wait(
    "(() => { const main = document.querySelector('main'); return main.scrollWidth <= main.clientWidth; })()",
  );
  await js("document.querySelector('[aria-label=\"Next entry\"]').click()");
  await showEntry("MINI-01", 3);
  await js("document.querySelector('[aria-label=\"GT: settings\"]').click()");
  const menu = '[role=dialog][aria-label="GT: settings"]';
  await wait(`document.querySelector(${JSON.stringify(menu)})`);
  for (const theme of THEMES) {
    await setTheme(theme);
    for (const tab of ["Look", "Filter", "Timing"]) {
      await js(
        `[...document.querySelectorAll(${JSON.stringify(`${menu} [role=tab]`)})].find((b) => b.textContent === '${tab}').click()`,
      );
      await wait(
        `document.querySelector(${JSON.stringify(`${menu} [role=tabpanel][aria-label=${tab}]`)})`,
      );
      await delay(100);
      await fits(
        menu,
        `The ${tab} tab of the lane menu must fit the 640x480 window at 200% zoom, ${theme}`,
      );
      assert.ok(
        await js(
          `document.querySelector(${JSON.stringify(menu)}).textContent.includes('Hide lane')`,
        ),
        "The menu keeps its Hide lane button",
      );
      assert.ok(
        await js(`(() => {
          const menu = document.querySelector(${JSON.stringify(menu)}).getBoundingClientRect();
          const pane = document.querySelector('[data-popover-bounds]').getBoundingClientRect();
          return menu.top >= pane.top - 0.5 && menu.bottom <= pane.bottom + 0.5 &&
            menu.left >= pane.left - 0.5 && menu.right <= pane.right + 0.5;
        })()`),
        `The ${tab} tab of the lane menu must stay inside the viewer, below the title bar, ${theme}`,
      );
      await shoot(`lane-menu-${tab.toLowerCase()}-${theme}-200.png`);
    }
    // A new lane starts as the saved default says, so the Timing tab says so until the lane differs, then offers to
    // save its settings.
    const sticky = `${menu} [role=group][aria-label=Sticky]`;
    const has = (text) =>
      `document.querySelector(${JSON.stringify(sticky)}).textContent.includes(${JSON.stringify(text)})`;
    // The menu scrolls at this size: the shots show the end of the Timing tab, where the button is.
    const toEnd = () =>
      js(`(() => {
        const menu = document.querySelector(${JSON.stringify(menu)});
        for (const element of [menu, ...menu.querySelectorAll('*')])
          if (element.scrollHeight > element.clientHeight && getComputedStyle(element).overflowY !== 'visible')
            element.scrollTop = element.scrollHeight;
      })()`);
    assert.ok(
      await js(has("Default for new views")),
      `A lane at the saved default says so, ${theme}`,
    );
    await toEnd();
    await shoot(`lane-menu-default-state-${theme}-200.png`);
    await timeout(3000);
    await wait(has("Make default"));
    await fits(
      menu,
      `The Timing tab with Make default must fit the 640x480 window at 200% zoom, ${theme}`,
    );
    await toEnd();
    await shoot(`lane-menu-make-default-${theme}-200.png`);
    await timeout(2050);
    await wait(has("Default for new views"));
  }
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
  await wait(`!document.querySelector(${JSON.stringify(menu)})`);
  await setView(window, [1100, 720], 1);
  await js("document.querySelector('[aria-label=Workspace]').click()");
};
