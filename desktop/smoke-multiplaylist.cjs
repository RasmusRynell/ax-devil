// The smoke test's several-playlists scenario: a resolver plugin that returns two playlists of one name is added
// through the Add Playlist dialog, which stays open and locked while the resolve is held in flight, shows a refusal
// and lets the user retry; once added, the sidebar tree nests the entries under a row per playlist. In both themes.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { SCREENSHOTS, SMOKE_ROOT, frames, page, setView } = require("./smoke-kit.cjs");

const VIDEO = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
const THEMES = ["light", "dark"];

const MANIFEST = {
  manifest: 2,
  id: "two-runs",
  name: "Two runs",
  version: "1.0.0",
  protocol: 3,
  command: ["python3", "runs.py"],
  code: ["runs.py"],
  idle_timeout_s: 30,
  handlers: [
    {
      kind: "playlist_resolver",
      id: "two-runs",
      name: "Two runs",
      description: "Two playlists of the same name",
      settings: [],
    },
  ],
};

const SCRIPT = `import json, sys

json.loads(sys.stdin.readline())
for run in ("Run", "Run"):
    print(json.dumps({"playlist": {"name": run}}), flush=True)
    for name in ("A", "B"):
        print(json.dumps({"entry": {"name": name, "video": {"file": ${JSON.stringify(VIDEO)}}}}), flush=True)
`;

module.exports.multiplaylist = async (window) => {
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
  const post = (url, body) =>
    js(
      `fetch(${JSON.stringify(url)}, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ${JSON.stringify(JSON.stringify(body))} }).then((r) => r.ok)`,
    );
  const clickButton = (label) =>
    js(
      `[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === '${label}').click()`,
    );
  const disabled = (label) =>
    js(
      `[...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent.startsWith('${label}')).control.matches(':disabled')`,
    );
  const choose = () =>
    js(`(() => {
      const select = document.querySelector('dialog[open] select');
      const option = [...select.options].find((o) => o.textContent === 'Two runs');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, option.value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
  const openDialog = async () => {
    await js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Playlist')).click()",
    );
    await wait("!!document.querySelector('dialog[open] select')");
    await choose();
    await wait("document.querySelector('dialog[open] select').value !== ''");
  };
  // Answers the next resolve requests as the test says: held until released, or refused.
  const intercept = (mode) =>
    js(`(() => {
      window.realFetch ??= window.fetch;
      window.fetch = (url, init) => {
        if (!String(url).endsWith('/resolve')) return window.realFetch(url, init);
        if (${JSON.stringify(mode)} === 'refuse')
          return Promise.resolve(new Response(
            JSON.stringify({ code: 'PlaylistInvalid', params: {}, reason: 'Two runs has nothing to open.' }),
            { status: 422 },
          ));
        return new Promise((release) => {
          window.release = () => release(window.realFetch(url, init));
        });
      };
    })()`);
  const restore = () => js("(() => { if (window.realFetch) window.fetch = window.realFetch; })()");

  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-runs-"));
  try {
    await fs.writeFile(path.join(folder, "ax-devil-plugin.json"), JSON.stringify(MANIFEST));
    await fs.writeFile(path.join(folder, "runs.py"), SCRIPT);
    assert.ok(await post("/api/plugins/install", { path: folder }), "The plugin installs");

    await setView(window, [1100, 720], 1);
    await openDialog();
    await eachTheme((theme) => shoot(`add-playlist-chosen-${theme}.png`));

    // Held in flight: the settings are locked, Name and Cancel are not, and the dialog says what it is doing.
    await intercept("hold");
    await clickButton("Add");
    await wait("/Finding playlists/.test(document.querySelector('dialog[open]').innerText)");
    assert.equal(await disabled("Playlist source"), true, "The source is locked while resolving");
    assert.equal(await disabled("Name"), false, "Name stays editable while resolving");
    await eachTheme((theme) => shoot(`add-playlist-finding-${theme}.png`));
    await clickButton("Cancel");
    await wait("!document.querySelector('dialog')");
    await js("window.release?.()");

    // A refusal stays in the dialog with the settings free again.
    await openDialog();
    await intercept("refuse");
    await clickButton("Add");
    await wait("document.querySelector('dialog[open] [role=alert]')");
    assert.equal(await disabled("Playlist source"), false, "A refusal frees the settings");
    await eachTheme((theme) => shoot(`add-playlist-refused-${theme}.png`));
    await clickButton("Cancel");
    await wait("!document.querySelector('dialog')");

    // Added for real: one item whose two playlists, "Run" and "Run (2)", are rows of their own.
    await restore();
    await openDialog();
    await clickButton("Add");
    await wait("!document.querySelector('dialog')");
    await wait(
      "[...document.querySelectorAll('[role=treeitem]')].some((row) => row.textContent.startsWith('Run (2)'))",
    );
    const rows = await js(
      "[...document.querySelectorAll('[role=treeitem]')].filter((row) => /^(Two runs|Run)/.test(row.textContent)).map((row) => [row.textContent, row.getAttribute('aria-level')])",
    );
    assert.deepEqual(
      rows.filter(([text]) => !text.startsWith("Two runs")).map(([, level]) => level),
      ["2", "2"],
      "Each playlist is a row under the item",
    );
    await eachTheme((theme) => shoot(`playlists-tree-${theme}.png`));

    await js(
      "[...document.querySelectorAll('[aria-label^=\"Close \"]')].find((b) => b.ariaLabel === 'Close Two runs')?.click()",
    );
    await post("/api/plugins/remove", { target: "two-runs" });
  } finally {
    await restore();
    await fs.rm(folder, { recursive: true, force: true });
  }
};
