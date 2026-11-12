// The smoke test's panels over the viewer, at the smallest window and 200% text size, in both themes: the page of a
// selected object in the media tools and a lane's settings menu in each of its tabs. Each must fit without anything clipped or sticking
// out, and a screenshot of each lands in the screenshots folder.
const { BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const fit = require("./fit.cjs");
const { delay, frames, moveTo, page, setView, settled, until } = require("./smoke-kit.cjs");

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
  const fits = async (selector, what, ellipsised = false) =>
    assert.deepEqual(
      await js(`(${fit.panel.toString()})(${JSON.stringify(selector)}, ${ellipsised})`),
      [],
      what,
    );
  const tools = () =>
    ["keyDown", "keyUp"].forEach((type) =>
      window.webContents.sendInputEvent({ type, keyCode: "B", modifiers: ["control"] }),
    );
  const PANEL = 'aside[aria-label="Media tools"]';
  const PAGE = `${PANEL} section[aria-label="Object page"]`;

  // The picture stays zoomed on the object while it is chosen and while the interface is at 200%.
  for (const theme of THEMES) {
    await setTheme(theme);
    await shoot(`zoomed-${theme}.png`);
  }
  await moveTo(window, ...point);
  for (const type of ["mouseDown", "mouseUp"]) click(type, point);
  await wait("document.querySelector('[data-outline=selected]')");
  await setView(window, [640, 480], 2);
  tools();
  await wait(`document.querySelector('${PAGE} [aria-busy=false]')`);
  for (const theme of THEMES) {
    await setTheme(theme);
    // Ids and values the page cuts with an ellipsis on purpose are not cut-off labels.
    await fits(PAGE, `The object page must fit the 640x480 window at 200% zoom, ${theme}`, true);
    await shoot(`inspector-${theme}-200.png`);
  }
  // At this size the media tools still sit inside the viewer, and their divider inside them.
  const docked = await js(`(() => {
    const aside = document.querySelector('${PANEL}').getBoundingClientRect();
    const pane = document.querySelector('[data-popover-bounds]').getBoundingClientRect();
    const divider = document.querySelector('${PANEL} [role=separator]').getBoundingClientRect();
    return aside.left >= pane.left - 1 && aside.right <= pane.right + 1 && divider.width > 0 && divider.left >= aside.left - 1;
  })()`);
  assert.ok(docked, "A narrow viewer's media tools and their divider must stay inside the viewer");
  await js(`document.querySelector('${PAGE} button[aria-label^=Back]').click()`);
  await wait(`!document.querySelector('${PAGE}')`);
  tools();
  await wait(`!document.querySelector('${PANEL}')`);

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
  // Every tab's shot starts at the top of the menu, whatever the tab before it scrolled to.
  const toStart = () =>
    js(`(() => {
      const menu = document.querySelector(${JSON.stringify(menu)});
      for (const element of [menu, ...menu.querySelectorAll('*')]) element.scrollTop = 0;
    })()`);
  // Capture the same controls at normal size before the minimum-size fit evidence.
  await setView(window, [1100, 720], 1);
  for (const theme of THEMES) {
    await setTheme(theme);
    for (const tab of ["Look", "Filter", "Timing"]) {
      await js(
        `[...document.querySelectorAll(${JSON.stringify(`${menu} [role=tab]`)})].find((b) => b.textContent === '${tab}').click()`,
      );
      await wait(
        `document.querySelector(${JSON.stringify(`${menu} [role=tabpanel][aria-label=${tab}]`)})`,
      );
      await toStart();
      await frames({ js });
      await shoot(`lane-menu-${tab.toLowerCase()}-${theme}.png`);
    }
  }
  await setView(window, [640, 480], 2);
  for (const theme of THEMES) {
    await setTheme(theme);
    for (const tab of ["Look", "Filter", "Timing"]) {
      await js(
        `[...document.querySelectorAll(${JSON.stringify(`${menu} [role=tab]`)})].find((b) => b.textContent === '${tab}').click()`,
      );
      await wait(
        `document.querySelector(${JSON.stringify(`${menu} [role=tabpanel][aria-label=${tab}]`)})`,
      );
      await toStart();
      await frames({ js });
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
      if (tab === "Look") {
        assert.equal(
          await js(`(() => {
          const control = document.querySelector(${JSON.stringify(`${menu} select`)});
          control.focus();
          return document.activeElement === control && control.getAttribute('aria-description')?.includes('How this lane draws');
        })()`),
          true,
          "Catalog help belongs to the focusable control at 200% zoom",
        );
      }
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

  // Copy the lane's catalog from its Look page, then delete the copy: the name field and the confirmation fit, the
  // dialog survives Escape and a press on it without the menu closing under it, and the lane ends where it began.
  const press = async (selector, text) => {
    window.focus();
    await until(() => window.isFocused(), "the panel window to have native focus");
    await wait("document.hasFocus()");
    const at = await settled(
      window,
      `(() => {
      const button = [...document.querySelectorAll(${JSON.stringify(`${selector} button`)})].find((b) => b.textContent === ${JSON.stringify(text)});
      button.scrollIntoView({ block: 'nearest' });
      const r = button.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    })()`,
    );
    await moveTo(window, ...at);
    for (const type of ["mouseDown", "mouseUp"]) click(type, at);
  };
  const pressAfterBlur = async (selector, text) => {
    // Windows can retain native focus after blur() when no other window takes it. Transfer focus between
    // our own windows, just as the multi-window smoke scenarios do, and keep both focus assertions.
    const other = new BrowserWindow({ width: 200, height: 100, show: false });
    try {
      await other.loadURL("about:blank");
      other.show();
      await until(
        () => other.isFocused() && !window.isFocused(),
        `the ${text} probe to transfer native focus to another window`,
      );
      await wait("!document.hasFocus()");
      await press(selector, text);
    } finally {
      other.destroy();
    }
  };
  const catalog = `document.querySelector(${JSON.stringify(`${menu} select`)}).value`;
  await js(
    `[...document.querySelectorAll(${JSON.stringify(`${menu} [role=tab]`)})].find((b) => b.textContent === 'Look').click()`,
  );
  await wait(`${catalog}.startsWith('built-in:')`);
  const before = await js(catalog);
  await press(menu, "Copy");
  await wait(`document.querySelector(${JSON.stringify(`${menu} input`)})`);
  for (const theme of THEMES) {
    await setTheme(theme);
    await frames({ js });
    await fits(
      menu,
      `The Look tab with the copy name must fit the 640x480 window at 200% zoom, ${theme}`,
    );
    await shoot(`lane-menu-copy-${theme}-200.png`);
  }
  await press(menu, "Create");
  await wait(`${catalog}.startsWith('user:')`);
  await wait(
    `[...document.querySelectorAll(${JSON.stringify(`${menu} button`)})].some((b) => b.textContent === 'Delete')`,
  );
  // The copy breaks on disk: the lane keeps drawing its last good catalog, but nothing may be saved, copied or applied
  // from it until it works again.
  const file = path.join(
    process.env.XDG_DATA_HOME,
    "ax-devil",
    "catalogs",
    (await js(catalog)).slice("user:".length),
  );
  const good = fs.readFileSync(file, "utf8");
  fs.writeFileSync(file, "not json");
  const disabled = (name) =>
    `[...document.querySelectorAll(${JSON.stringify(`${menu} button`)})].find((b) => b.textContent === ${JSON.stringify(name)}).disabled`;
  await wait(
    `document.querySelector(${JSON.stringify(`${menu} select`)}).selectedOptions[0].textContent.includes('(invalid)')`,
  );
  for (const name of ["Use as default", "Copy", "Apply to all lanes"])
    assert.ok(await js(disabled(name)), `${name} is disabled while the lane's catalog is invalid`);
  for (const theme of THEMES) {
    await setTheme(theme);
    await toStart();
    await frames({ js });
    await shoot(`lane-menu-invalid-${theme}-200.png`);
  }
  fs.writeFileSync(file, good);
  await wait(`!${disabled("Copy")}`);
  await press(menu, "Delete");
  await wait("document.querySelector('dialog[open]')");
  for (const theme of THEMES) {
    await setTheme(theme);
    await frames({ js });
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `The delete confirmation must fit the 640x480 window at 200% zoom, ${theme}`,
    );
    await shoot(`lane-menu-delete-${theme}-200.png`);
  }
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
  await delay(300);
  assert.ok(
    await js(
      `Boolean(document.querySelector('dialog[open]') && document.querySelector(${JSON.stringify(menu)}))`,
    ),
    "Escape must neither answer the confirmation nor close the lane menu under it",
  );
  await press("dialog[open]", "Delete");
  await wait("!document.querySelector('dialog[open]')");
  await wait(`${catalog} === ${JSON.stringify(before)}`);
  assert.ok(
    await js(`Boolean(document.querySelector(${JSON.stringify(menu)}))`),
    "The lane menu stays open after the delete",
  );

  await wait(`!${disabled("Apply to all lanes")}`);
  // An action-only transport refusal uses the lane's standard notice presentation and can be dismissed.
  await js(`(() => {
    const real = window.fetch;
    window.fetch = (url, init) => {
      if (url !== '/api/catalogs/reload') return real(url, init);
      window.fetch = real;
      return Promise.resolve(new Response(JSON.stringify({ code: 'CatalogInvalid', params: {}, reason: 'Reload refused' }), { status: 400 }));
    };
  })()`);
  await pressAfterBlur(menu, "Apply to all lanes");
  await wait(`!document.querySelector(${JSON.stringify(menu)})`);
  await wait(`document.querySelector('[aria-label="GT: warnings"]')`);
  await js(`document.querySelector('[aria-label="GT: warnings"]').click()`);
  const warnings = '[role=dialog][aria-label="GT: warnings"]';
  await wait(
    `document.querySelector(${JSON.stringify(warnings)}).textContent.includes('Reload refused')`,
  );
  for (const theme of THEMES) {
    await setTheme(theme);
    await fits(warnings, `The Apply refusal must fit at 200% zoom, ${theme}`);
    await frames({ js });
    await shoot(`catalog-apply-refused-${theme}-200.png`);
  }
  await setView(window, [1100, 720], 1);
  for (const theme of THEMES) {
    await setTheme(theme);
    await frames({ js });
    await shoot(`catalog-apply-refused-${theme}.png`);
  }
  await pressAfterBlur(warnings, "Dismiss Apply error");
  await wait(`!document.querySelector('[aria-label="GT: warnings"]')`);
  await js("document.querySelector('[aria-label=Workspace]').click()");
};
