// The smoke test's Events scenario on the real canvas: a video with an ADF file whose frames 3, 6 and 8 hold events (cars
// renamed and ended; two on frame 3). The Events tab lists them with the frame's events tinted and the line before the
// ones still to come; an event's page leads to its objects and an object's page back to its events; Go to frame seeks.
// Shown in both themes, then in the smallest window at 200% zoom, where the panel must fit.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, SHOWN, SMOKE_ROOT, goToFrame, page, setView } = require("./smoke-kit.cjs");
const { VIDEO, inBackendWindow } = require("./smoke-twice.cjs");

const PANEL = 'aside[aria-label="Media tools"]';
const LIST = `${PANEL} [role=listbox][aria-label=Events]`;
const EVENT_PAGE = `${PANEL} section[aria-label="Event page"]`;
const OBJECT_PAGE = `${PANEL} section[aria-label="Object page"]`;
const INVOLVED = `${EVENT_PAGE} [role=listbox][aria-label="Objects involved"]`;
const OBJECT_EVENTS = `${OBJECT_PAGE} section[aria-label=Events]`;
const SEARCH = `${PANEL} input[aria-label="Search events"]`;

module.exports.run = () =>
  inBackendWindow(
    [
      "--video",
      VIDEO,
      "--overlay",
      path.join(SMOKE_ROOT, "fixtures/decoders/adf/events_v1.jsonl"),
      "--handler-type",
      "ADF_V1_FRAME",
    ],
    async (window) => {
      const { js, wait } = page(window);
      await setView(window, [1500, 720], 1);
      const settle = () =>
        js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
      const inBothThemes = async (name) => {
        for (const theme of ["light", "dark"]) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await settle();
          await fs.writeFile(
            path.join(SCREENSHOTS, `${name}-${theme}.png`),
            (await window.webContents.capturePage()).toPNG(),
          );
        }
      };
      const fits = async (what) =>
        assert.deepEqual(
          await js(`(${fit.panel.toString()})(${JSON.stringify(PANEL)}, true)`),
          [],
          what,
        );
      const count = (selector) =>
        js(`document.querySelectorAll(${JSON.stringify(selector)}).length`);
      const rows = () =>
        js(`[...document.querySelectorAll(${JSON.stringify(`${LIST} [role=option]`)})].map((row) => ({
          text: row.innerText.replaceAll('\\n', ' '),
          current: row.getAttribute('aria-current') === 'true',
          marked: row.getAttribute('aria-selected') === 'true',
        }))`);
      const noted = (text) =>
        wait(
          `[...document.querySelectorAll('${PANEL} [role=status]')].some((note) => note.textContent === ${JSON.stringify(text)})`,
        );
      const typeIn = async (text) => {
        await js(`document.querySelector('${SEARCH}').focus()`);
        window.webContents.insertText(text);
      };
      // Empties the search box the way React sees an edit, with no shortcut: Ctrl+A is not select-all on macOS.
      const clearSearch = async () => {
        await js(
          `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('${SEARCH}'), '')`,
        );
        await js(
          `document.querySelector('${SEARCH}').dispatchEvent(new Event('input', { bubbles: true }))`,
        );
      };
      const back = () => js(`document.querySelector('${PANEL} button[aria-label^=Back]').click()`);

      await wait(`${SHOWN} >= 0`);
      await goToFrame(window, 3);
      for (const type of ["keyDown", "keyUp"])
        window.webContents.sendInputEvent({ type, keyCode: "B", modifiers: ["control"] });
      await wait(`document.querySelector('${PANEL}')`);
      await js(
        `[...document.querySelectorAll('${PANEL} [role=tab]')].find((tab) => tab.textContent.startsWith('Events')).click()`,
      );
      await wait(`document.querySelectorAll('${LIST} [role=option]').length === 4`);

      // The tab counts the events; the two of frame 3 are tinted and the line lies after them.
      assert.match(
        await js(`document.querySelector('${PANEL} [role=tab][aria-selected=true]').textContent`),
        /^Events\s*4$/,
      );
      const listed = await rows();
      assert.deepEqual(
        listed.map((row) => row.current),
        [true, true, false, false],
      );
      assert.match(listed[0].text, /rename.*car-1, car-4/);
      assert.equal(await count(`${LIST} [data-now]`), 1);
      await inBothThemes("events-list");

      // A search narrows the list and says how many it kept; a field no event has is named.
      await typeIn("kind:delete");
      await wait(`document.querySelectorAll('${LIST} [role=option]').length === 2`);
      await noted("2 of 4");
      await inBothThemes("events-search");
      await clearSearch();
      await typeIn("colour:red");
      await noted("No event has colour");
      await clearSearch();
      await wait(`document.querySelectorAll('${LIST} [role=option]').length === 4`);

      // The line moves with the video: on frame 6 the third event is the frame's.
      await goToFrame(window, 6);
      await wait(`document.querySelector('${LIST} [role=option][aria-current=true]')`);
      assert.deepEqual(
        (await rows()).map((row) => row.current),
        [false, false, true, false],
      );

      // An event's page: its objects with where they appear, and the other event of its moment.
      await js(`document.querySelector('${LIST} [role=option]').click()`);
      await wait(`document.querySelector('${INVOLVED}')`);
      await wait(`document.querySelector('${EVENT_PAGE} [aria-label="Same moment"]')`);
      await wait(`document.querySelector('${EVENT_PAGE} [data-tick]')`);
      assert.deepEqual(
        await js(
          `[...document.querySelectorAll('${INVOLVED} [role=option]')].map((row) => row.firstElementChild.textContent)`,
        ),
        ["car-1", "car-4"],
      );
      await inBothThemes("event-page");
      // Go to frame seeks to the event's frame.
      await js(
        `[...document.querySelectorAll('${EVENT_PAGE} button')].find((b) => b.textContent === 'Go to frame').click()`,
      );
      await wait(`${SHOWN} === 3`);

      // An involved object's page lists the events that involve it, and marks them on its strip.
      await js(`document.querySelectorAll('${INVOLVED} [role=option]')[1].click()`);
      await wait(`document.querySelector('${OBJECT_EVENTS} [role=option]')`);
      await wait(`document.querySelector('${OBJECT_PAGE} [data-tick]')`);
      assert.equal(await count(`${OBJECT_EVENTS} [role=option]`), 2);
      await inBothThemes("object-events");
      // One of them leads to its page. Back is one page at a time: the event, the object, the first event, the list.
      await js(`document.querySelector('${OBJECT_EVENTS} [role=option]').click()`);
      await wait(`document.querySelector('${EVENT_PAGE}')`);
      await back();
      await wait(`document.querySelector('${OBJECT_PAGE}')`);
      await back();
      await wait(
        `document.querySelector('${EVENT_PAGE}') && !document.querySelector('${OBJECT_PAGE}')`,
      );
      await back();
      await wait(`!document.querySelector('${EVENT_PAGE}')`);
      assert.ok(
        (await rows()).some((row) => row.marked),
        "Back marks the row of the page it left",
      );

      // The smallest window at 200%: the list and an event's page fit.
      await js("document.querySelector('[aria-label=Workspace]').click()");
      await setView(window, [640, 480], 2);
      await fits("The Events list must fit the 640x480 window at 200% zoom");
      await inBothThemes("events-200");
      await js(`document.querySelector('${LIST} [role=option]').click()`);
      await wait(`document.querySelector('${EVENT_PAGE} [aria-label="Same moment"]')`);
      await fits("An event's page must fit the 640x480 window at 200% zoom");
      await inBothThemes("event-page-200");
      await setView(window, [1500, 720], 1);
    },
  );
