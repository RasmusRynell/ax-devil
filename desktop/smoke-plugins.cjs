// The smoke test's Plugins view: only the built-in plugins to begin with, then one installed through the view (with a handler it skips) and one
// that stopped loading because its manifest was damaged after it was installed, in both themes at the smallest window
// and 200% zoom. Updating the first after editing its manifest shows the new version. Removing both leaves the registry as it was.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, SMOKE_ROOT, button, frames, page, setView } = require("./smoke-kit.cjs");

const THEMES = ["light", "dark"];

module.exports.plugins = async (window) => {
  const { js, wait } = page(window);
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const eachTheme = async (visit) => {
    for (const theme of THEMES) {
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames({ js });
      await visit(theme);
    }
  };
  const fits = async (what) =>
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Plugins view must fit: ${what}`,
    );
  // A list that grows with the user's plugins scrolls, so only its width and the install form are held to the window.
  const keepsWidth = async (what) =>
    assert.ok(
      await js(`(() => {
        const aside = document.querySelector('aside');
        const install = [...aside.querySelectorAll('button')].find((b) => b.textContent === 'Install').getBoundingClientRect();
        return aside.scrollWidth <= aside.clientWidth && install.bottom <= innerHeight && install.right <= innerWidth;
      })()`),
      `The Plugins view must not overflow sideways or push the install form out: ${what}`,
    );

  // Opens the Plugins view, which a second click on its button would close.
  const show = async () => {
    await js("document.querySelector('[aria-label=Plugins][aria-pressed=false]')?.click()");
    await wait("document.querySelector('aside section[aria-label=\"Built-in\"]')");
  };

  const source = path.join(SMOKE_ROOT, "fixtures/plugins/shell-times");
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-plugins-"));
  try {
    const good = path.join(folder, "good");
    const broken = path.join(folder, "broken");
    await fs.cp(source, good, { recursive: true });
    await fs.cp(source, broken, { recursive: true });
    // A handler of a kind this build does not know is listed as skipped.
    const goodManifest = path.join(good, "ax-devil-plugin.json");
    const goodJson = JSON.parse(await fs.readFile(goodManifest, "utf8"));
    goodJson.handlers.push({ kind: "encoder", id: "shell-times-encode" });
    await fs.writeFile(goodManifest, JSON.stringify(goodJson, null, 2));
    const manifest = path.join(broken, "ax-devil-plugin.json");
    await fs.writeFile(
      manifest,
      (await fs.readFile(manifest, "utf8")).replaceAll("shell-times", "broken-times"),
    );

    await setView(window, [640, 480], 2);
    await show();
    await eachTheme(async (theme) => {
      await fits(`empty, ${theme}, 640x480 at 200%`);
      await shoot(`plugins-empty-${theme}-200.png`);
    });

    // The person types a folder, which the backend installs and lists at once.
    await js("document.querySelector('aside input').focus()");
    window.webContents.insertText(good);
    await js(`${button("Install")}.click()`);
    await wait("document.querySelector('aside section[aria-label=Installed]')");

    // A second plugin whose manifest breaks afterwards stays listed as failed, with the reason.
    const install = (target) =>
      js(
        `fetch('/api/plugins/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: ${JSON.stringify(target)} }) }).then((r) => r.ok)`,
      );
    assert.ok(await install(broken), "The second plugin installs");
    await fs.writeFile(manifest, "{ not json");
    // The running app notices the edited manifest on its own; the view looks again when it is shown.
    for (let i = 0; i < 50; i++) {
      await js("document.querySelector('[aria-label=Workspace][aria-pressed=false]')?.click()");
      await show();
      if (await js("!!document.querySelector('aside section[aria-label=Failed]')")) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    await wait("document.querySelector('aside section[aria-label=Failed] [role=alert]')");
    await eachTheme(async (theme) => {
      await keepsWidth(`${theme}, 640x480 at 200%`);
      await shoot(`plugins-${theme}-200.png`);
    });

    await setView(window, [1100, 720], 1);
    await eachTheme(async (theme) => {
      await fits(`${theme}, 1100x720`);
      await shoot(`plugins-${theme}.png`);
    });
    // Update reads an edited manifest again.
    goodJson.version = "1.1.0";
    await fs.writeFile(goodManifest, JSON.stringify(goodJson, null, 2));
    await js(`document.querySelector('aside button[aria-label="Update Shell times"]').click()`);
    await wait(
      "[...document.querySelectorAll('aside section[aria-label=Installed] li')].some((li) => li.textContent.includes('1.1.0'))",
    );
    for (const name of ["Remove broken", "Remove Shell times"]) {
      await js(`document.querySelector('aside button[aria-label="${name}"]').click()`);
      await wait(`!document.querySelector('aside button[aria-label="${name}"]')`);
    }
    assert.ok(
      await js("!document.querySelector('aside section[aria-label=Installed]')"),
      "Removing both plugins leaves only the built-ins",
    );
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
  await js("document.querySelector('[aria-label=Workspace][aria-pressed=false]')?.click()");
};
