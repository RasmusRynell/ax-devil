// Inspection continuation through a real CVAT file, including the bounded copy controls.
const { nativeTheme, clipboard } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const fit = require("./fit.cjs");
const { SCREENSHOTS, SHOWN, page, setView, settled, goToFrame } = require("./smoke-kit.cjs");
const { VIDEO, inBackendWindow } = require("./smoke-twice.cjs");

const INSPECTOR = 'section[aria-label="Object page"]';

async function openObject(window) {
  const { js, wait } = page(window);
  await wait(`${SHOWN} >= 0`);
  await goToFrame(window, 0);
  for (const type of ["keyDown", "keyUp"])
    window.webContents.sendInputEvent({ type, keyCode: "B", modifiers: ["control"] });
  await wait("document.querySelector('aside[aria-label=\"Media tools\"] [role=option]')");
  await js("document.querySelector('aside[aria-label=\"Media tools\"] [role=listbox]').focus()");
  for (const keyCode of ["ArrowDown", "Return"])
    for (const type of ["keyDown", "keyUp"]) window.webContents.sendInputEvent({ type, keyCode });
}

async function shot(window, name) {
  await settled(
    window,
    `(() => { const r = document.querySelector('${INSPECTOR}').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`,
  );
  await fs.writeFile(
    path.join(SCREENSHOTS, name),
    (await window.webContents.capturePage()).toPNG(),
  );
}

module.exports.run = async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "inspection-"));
  const overlay = path.join(folder, "fields.xml");
  const fields = Array.from({ length: 5000 }, (_, i) => `<field${i}>${i}</field${i}>`).join("");
  await fs.writeFile(
    overlay,
    `<annotations><meta><task><size>1</size><mode>interpolation</mode><original_size><width>64</width><height>48</height></original_size></task></meta><track id="7" label="Car"><box frame="0" outside="0" xtl="16" ytl="12" xbr="48" ybr="36"><extra><value>${"x".repeat(16 * 1024 * 1024)}😀</value>${fields}</extra></box></track></annotations>`,
  );
  try {
    await inBackendWindow(
      ["--video", VIDEO, "--overlay", overlay, "--handler-type", "CVAT"],
      async (window) => {
        const { js, wait } = page(window);
        await setView(window, [1500, 720], 1);
        await openObject(window);
        await wait(`document.querySelector('${INSPECTOR} [data-cursor-id="inspection:next"]')`);
        await js(`document.querySelector('${INSPECTOR} [data-group=debug] button').click()`);
        const shoot = (name) => shot(window, name);
        for (const theme of ["light", "dark"]) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await shoot(`inspection-${theme}.png`);
        }
        await js("document.querySelector('[aria-label=Workspace]').click()");
        for (const zoom of [1, 2]) {
          await setView(window, [640, 480], zoom);
          for (const theme of ["light", "dark"]) {
            nativeTheme.themeSource = theme;
            await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
            await shoot(`inspection-min-${zoom * 100}-${theme}.png`);
            assert.deepEqual(
              await js(`(${fit.panel.toString()})(${JSON.stringify(INSPECTOR)}, true)`),
              [],
            );
          }
        }
        await js(
          `document.querySelector('${INSPECTOR} [data-cursor-id="inspection:next"]').click()`,
        );
        await wait(`document.querySelector('${INSPECTOR} [data-cursor-id="inspection:first"]')`);
        await js(`document.querySelector('${INSPECTOR} .overflow-y-auto').scrollTo(0, 1e9)`);
        await wait(`document.querySelector('${INSPECTOR}').innerText.includes('field4999')`);
        await js(
          `document.querySelector('${INSPECTOR} [data-cursor-id="inspection:first"]').click()`,
        );
        await wait(`document.querySelector('${INSPECTOR} [data-cursor-id="inspection:next"]')`);
        await js(`document.querySelector('${INSPECTOR} .overflow-y-auto').scrollTo(0, 0)`);
        // Scroll just enough to reveal the first source value; the position comes from measured row height.
        await js(
          `(() => { const list = document.querySelector('${INSPECTOR} .overflow-y-auto'); const row = list.querySelector('[role=listitem]'); list.scrollTop = row.getBoundingClientRect().height * 10; })()`,
        );
        await wait(`document.querySelector('${INSPECTOR} [data-field$="value"] [data-act=copy]')`);
        await js(
          `document.querySelector('${INSPECTOR} [data-field$="value"] [data-act=copy]').click()`,
        );
        await wait(`document.querySelector('${INSPECTOR} [data-cursor-id="inspection:cancel"]')`);
        assert.deepEqual(
          await js(`(${fit.panel.toString()})(${JSON.stringify(INSPECTOR)}, true)`),
          [],
        );
        await shoot("inspection-copy-200-dark.png");
        await js(
          `document.querySelector('${INSPECTOR} [data-cursor-id="inspection:cancel"]').click()`,
        );
        await wait(`!document.querySelector('${INSPECTOR} [data-cursor-id="inspection:cancel"]')`);
      },
    );
    const rawFile = path.join(folder, "raw.jsonl");
    const reason = `${"x".repeat(16 * 1024 * 1024)}é😀`;
    await fs.writeFile(
      rawFile,
      JSON.stringify({
        id: "7",
        classes: [{ type: "Car", score: 0.9 }],
        path: [
          {
            timestamp: "1970-01-01T00:00:00Z",
            bounding_box: { left: 0.25, top: 0.25, right: 0.75, bottom: 0.75 },
          },
        ],
        start_time: "1970-01-01T00:00:00Z",
        end_time: "1970-01-01T00:00:00Z",
        end_reason: reason,
      }),
    );
    await inBackendWindow(
      ["--video", VIDEO, "--overlay", rawFile, "--handler-type", "ADF_V1_CONSOLIDATED"],
      async (window) => {
        const { js, wait } = page(window);
        await setView(window, [1500, 720], 1);
        await openObject(window);
        await wait(`document.querySelector('${INSPECTOR} [data-group=raw]')`);
        await js(`document.querySelector('${INSPECTOR} [data-group=raw] button').click()`);
        await js(`document.querySelector('${INSPECTOR} [aria-label="Copy Raw"]').focus()`);
        for (const theme of ["light", "dark"]) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await shot(window, `inspection-raw-${theme}.png`);
        }
        await js("document.querySelector('[aria-label=Workspace]').click()");
        await setView(window, [640, 480], 2);
        await js(
          `document.querySelector('${INSPECTOR} [aria-label="Copy Raw"]').scrollIntoView({block: "center"})`,
        );
        await js(`document.querySelector('${INSPECTOR} [aria-label="Copy Raw"]').click()`);
        await wait(`document.querySelector('${INSPECTOR} [data-cursor-id="inspection:cancel"]')`);
        assert.deepEqual(
          await js(`(${fit.panel.toString()})(${JSON.stringify(INSPECTOR)}, true)`),
          [],
        );
        await shot(window, "inspection-raw-copy-200-dark.png");
        await wait(`document.querySelector('${INSPECTOR}').innerText.includes('Copied')`);
        const expected = JSON.stringify(
          { end_reason: reason, timestamp: "1970-01-01T00:00:00+00:00" },
          null,
          2,
        ).replace(
          /[\u007f-\uffff]/g,
          (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
        );
        const hash = (text) => createHash("sha256").update(text).digest("hex");
        assert.equal(
          hash(await clipboard.readText()),
          hash(expected),
          "Raw copies the complete JSON, including the value absent from its preview",
        );
      },
    );
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
};
