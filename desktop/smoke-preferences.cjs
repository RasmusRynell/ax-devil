// A recovered preference edited back to its displayed default must actually be repaired on disk and after restart.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { page, SCREENSHOTS } = require("./smoke-kit.cjs");
const { VIDEO, inBackendWindow } = require("./smoke-twice.cjs");

module.exports.run = async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-preferences-"));
  const file = path.join(folder, "settings.json");
  const neighbor = { enabled: false, timeout_ms: "damaged", opacity: 0.4 };
  const raw = {
    version: 1,
    shortcuts: { exit: "Ctrl+" },
    settings: {
      "ui.quick_setup_done": true,
      "scan.on_startup": false,
      "playback.video_cache": "invalid",
      "overlays.default_sticky": neighbor,
    },
  };
  const read = async () => JSON.parse(await fs.readFile(file, "utf8"));
  try {
    await fs.writeFile(file, JSON.stringify(raw));
    for (const recovered of [true, false]) {
      const before = await fs.readFile(file, "utf8");
      await inBackendWindow(["--config", file, "--video", VIDEO], async (window) => {
        const { js, wait } = page(window);
        const state = () => js("fetch('/api/settings').then((r) => r.json())");
        assert.equal(
          (await state()).recovered?.includes("playback.video_cache") ?? false,
          recovered,
        );
        await js("document.querySelector('[aria-label=Settings]').click()");
        await wait("document.querySelector('dialog[open] h2')?.textContent === 'Settings'");
        if (recovered) {
          await js(
            "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Keyboard shortcuts').click()",
          );
          await wait("document.querySelector('button[aria-label=\"Reset Exit\"]:not(:disabled)')");
          const themeBefore = nativeTheme.themeSource;
          try {
            for (const theme of ["light", "dark"]) {
              nativeTheme.themeSource = theme;
              await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
              await js(
                "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))",
              );
              await fs.writeFile(
                path.join(SCREENSHOTS, `recovered-shortcuts-${theme}.png`),
                (await window.webContents.capturePage()).toPNG(),
              );
            }
          } finally {
            nativeTheme.themeSource = themeBefore;
          }
          await js("document.querySelector('button[aria-label=\"Reset Exit\"]').click()");
          await js(
            "[...document.querySelectorAll('dialog')].find((d) => d.querySelector('h2')?.textContent === 'Keyboard Shortcuts').querySelector('button[type=submit]').click()",
          );
          await wait("document.querySelectorAll('dialog[open]').length === 1");
          assert.equal((await state()).recoveredShortcuts?.includes("exit") ?? false, false);
        }
        await js(
          "[...document.querySelectorAll('[role=tab]')].find((t) => t.textContent === 'Playback').click()",
        );
        for (const mode of ["manual", "auto"]) {
          await js(
            `(() => { const select = document.querySelector('dialog select'); select.value = '${mode}'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`,
          );
          await wait(`document.querySelector('dialog select').value === '${mode}'`);
        }
        await js(
          "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'OK').click()",
        );
        await wait("!document.querySelector('dialog[open]')");
        assert.equal((await state()).values["playback.video_cache"], "auto");
        assert.equal((await state()).recovered?.includes("playback.video_cache") ?? false, false);
      });
      assert.equal((await read()).settings["playback.video_cache"], undefined);
      assert.deepEqual((await read()).settings["overlays.default_sticky"], neighbor);
      if (!recovered)
        assert.equal(
          await fs.readFile(file, "utf8"),
          before,
          "a valid choice edited away and back does not write",
        );
    }
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
};
