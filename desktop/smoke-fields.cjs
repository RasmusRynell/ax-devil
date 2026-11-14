// The smoke test's object page scenario on the real canvas: an object with colours, a motion state and a picture, in both
// themes. No single decoder gives one object all of these, so the video has two lanes: an ADF v1 frame file, whose car is
// moving, and an ADF beta frame file, whose car has a picture. Both name their colours and score the class.
const { pressShortcut } = require("./shortcut-input.cjs");
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  SHOWN,
  SMOKE_ROOT,
  colourCount,
  delay,
  goToFrame,
  laneCells,
  page,
  setView,
  settled,
  until,
} = require("./smoke-kit.cjs");
const { VIDEO, inBackendWindow } = require("./smoke-twice.cjs");

const ADF = path.join(SMOKE_ROOT, "fixtures/decoders/adf");
const PANEL = 'aside[aria-label="Media tools"]';
// The object's page in the media tools.
const INSPECTOR = `${PANEL} section[aria-label="Object page"]`;

module.exports.run = () =>
  inBackendWindow(
    [
      "--video",
      VIDEO,
      "--overlay",
      path.join(ADF, "inspector_v1.jsonl"),
      "--overlay",
      path.join(ADF, "inspector_beta.jsonl"),
      "--handler-type",
      "ADF_V1_FRAME",
      "--handler-type",
      "ADF_BETA_FRAME",
    ],
    async (window) => {
      const { js, wait } = page(window);
      await setView(window, [1500, 720], 1);
      const settle = () =>
        js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
      const shoot = async (name) => {
        await settle();
        await fs.writeFile(
          path.join(SCREENSHOTS, name),
          (await window.webContents.capturePage()).toPNG(),
        );
      };
      const inBothThemes = async (name) => {
        for (const theme of ["light", "dark"]) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await shoot(`${name}-${theme}.png`);
        }
      };
      // A real left click on the middle of a lane's picture, where both cars are. Opening or closing the media tools
      // resizes the canvas at once, but the lane cells follow a moment later; until the last cell ends where the canvas
      // does, and the click point has stopped moving, a click lands where the lanes used to be.
      const select = async (lane) => {
        // The pane follows the area a frame behind, and the lanes follow the pane: wait for the pane to fill the area.
        await wait(`(() => {
          const area = document.querySelector('main > div');
          const pane = document.querySelector('[role=group][data-cursor-id^="pane:"]');
          return Math.abs(pane.getBoundingClientRect().width - area.clientWidth) <= 1;
        })()`);
        await wait(`(() => {
          const cells = document.querySelectorAll('[data-cursor-id^="frame:"]');
          const end = document.querySelector('canvas[role=img]').getBoundingClientRect().right;
          return Math.abs(cells[cells.length - 1].getBoundingClientRect().right - end) <= 1;
        })()`);
        const { x, y } = await settled(
          window,
          `(() => {
          const r = document.querySelectorAll('[data-cursor-id^="frame:"]')[${lane}].getBoundingClientRect();
          return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
        })()`,
        );
        for (const type of ["mouseMove", "mouseDown", "mouseUp"])
          window.webContents.sendInputEvent({ type, x, y, button: "left", clickCount: 1 });
        await wait(`document.querySelector('${INSPECTOR} [aria-busy=false]')`);
      };
      const inspector = () => js(`document.querySelector('${INSPECTOR}')?.innerText ?? ''`);

      await wait(`${SHOWN} >= 0`);
      await goToFrame(window, 0);
      // The media tools show the page of the object picked on a picture.
      pressShortcut(window, "Ctrl+B");
      await wait(`document.querySelector('${PANEL}')`);
      const cells = await until(async () => {
        const found = await laneCells(window);
        return found.length === 2 && found;
      }, "two lanes");
      for (const cell of cells) {
        const deadline = Date.now() + 5000;
        while ((await colourCount(window, cell)) === 0 && Date.now() < deadline) await delay(100);
        assert.ok(await colourCount(window, cell), `${cell.label}: no boxes on frame 0`);
      }

      // The first lane's car: its class and score, its colours as swatches under their attribute, and its motion.
      await select(0);
      const moving = await inspector();
      assert.match(moving, /^Objects\ncar-1\ncar\nscore 0\.87\nmoving\n/);
      assert.match(moving, /Classification \d+\n(?:.*\n)*?score\n0\.87\n/i);
      assert.match(moving, /vehicle colors\nBlue\n0\.72\n/);
      assert.ok(
        (await js(`document.querySelectorAll('${INSPECTOR} [title^="#"]').length`)) >= 3,
        "Each colour has a swatch",
      );
      await inBothThemes("inspector-fields");

      // The smallest window at normal zoom and at 200%, with the sidebar closed: the page fits, apart from the
      // values it cuts with an ellipsis on purpose (the id's middle, a long value's end).
      await js("document.querySelector('[aria-label=Workspace]').click()");
      for (const zoom of [1, 2]) {
        await setView(window, [640, 480], zoom);
        await wait(`document.querySelector('${INSPECTOR} [aria-busy=false]')`);
        // The list holds only the rows near the top until it scrolls: check it at the top and at the end.
        for (const end of [false, true]) {
          await js(
            `document.querySelector('${INSPECTOR} .overflow-y-auto').scrollTo(0, ${end ? 1e6 : 0})`,
          );
          await settle();
          assert.deepEqual(
            await js(`(${fit.panel.toString()})(${JSON.stringify(INSPECTOR)}, true)`),
            [],
            `The object page must fit the 640x480 window at ${zoom * 100}% zoom${end ? ", scrolled to its end" : ""}`,
          );
        }
        await js(`document.querySelector('${INSPECTOR} .overflow-y-auto').scrollTo(0, 0)`);
        await settle();
        if (zoom === 2) await inBothThemes("inspector-fields-200");
      }
      await setView(window, [1500, 720], 1);
      await js("document.querySelector('[aria-label=Workspace]').click()");

      // The second lane's car has a picture, folded until its caption is opened.
      await js(`document.querySelector('${INSPECTOR} button[aria-label^=Back]').click()`);
      await wait(`!document.querySelector('${INSPECTOR}')`);
      await select(1);
      const closed = await inspector();
      assert.match(closed, /Images \d+/i);
      assert.doesNotMatch(closed, /bytes/);
      await inBothThemes("inspector-images-folded");
      await js(`document.querySelector('${INSPECTOR} [data-group=images] button').click()`);
      await wait(`document.querySelector('${INSPECTOR}').innerText.includes('bytes')`);
      await inBothThemes("inspector-images");

      // On the next frame the object is not there: the page keeps its name, says why and offers the frame it was last on.
      await goToFrame(window, 1);
      await wait(`document.querySelector('${INSPECTOR}').innerText.includes('Not in this frame')`);
      await wait(`document.querySelector('${INSPECTOR}').innerText.includes('Go to last seen')`);
      assert.match(await inspector(), /Last seen at 00:00:00\.\d{3} \(\d\.\d s ago\)/);
      await inBothThemes("inspector-absent");

      // Back on frame 0 a class toggle hides the object: the page says so and keeps its fields out.
      await goToFrame(window, 0);
      await wait(`!document.querySelector('${INSPECTOR}').innerText.includes('Not in this frame')`);
      await js("document.querySelectorAll('[aria-label$=\": settings\"]')[1].click()");
      await wait("document.querySelector('[role=dialog][aria-label$=\": settings\"]')");
      await js(
        `[...document.querySelectorAll('[role=dialog] [role=tab]')].find((tab) => tab.textContent === 'Filter').click()`,
      );
      await js(
        `[...document.querySelectorAll('[role=dialog] label')].find((label) => label.innerText === 'Cars').click()`,
      );
      await wait(`document.querySelector('${INSPECTOR}').innerText.includes('Hidden by filter')`);
      pressShortcut(window, "Escape");
      await wait("!document.querySelector('[role=dialog]')");
      await inBothThemes("inspector-hidden");
    },
  );
