// The smoke test's several-playlists scenario: a resolver plugin that returns two playlists of one name is added
// through the Add Playlist dialog, which stays open and locks its source while the resolve is held in flight; once
// added, the sidebar tree has a top-level row per playlist, with its entries inside. No screenshots: the unit tests own the
// dialog's states, and the smoke has little time to spare.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { SMOKE_ROOT, page } = require("./smoke-kit.cjs");

const VIDEO = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");

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
  const post = (url, body) =>
    js(
      `fetch(${JSON.stringify(url)}, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ${JSON.stringify(JSON.stringify(body))} }).then((r) => r.ok)`,
    );
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-runs-"));
  try {
    await fs.writeFile(path.join(folder, "ax-devil-plugin.json"), JSON.stringify(MANIFEST));
    await fs.writeFile(path.join(folder, "runs.py"), SCRIPT);
    assert.ok(await post("/api/plugins/install", { path: folder }), "The plugin installs");

    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Playlist')).click()",
    );
    await wait("document.querySelector('dialog[open] select')");
    await js(`(() => {
      const select = document.querySelector('dialog[open] select');
      const option = [...select.options].find((o) => o.textContent === 'Two runs');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, option.value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);

    // The resolve is held in flight: the dialog stays open, says so, and locks the source but not the name.
    await js(`(() => {
      window.realFetch = window.fetch;
      window.fetch = (url, init) => String(url).endsWith('/resolve')
        ? new Promise((release) => { window.release = () => release(window.realFetch(url, init)); })
        : window.realFetch(url, init);
    })()`);
    await js(
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Load Playlist').click()",
    );
    await wait("/Finding playlists/.test(document.querySelector('dialog[open]').innerText)");
    assert.equal(
      await js(
        "document.querySelector('dialog[open] select').matches(':disabled') && !document.querySelector('dialog[open] input').matches(':disabled')",
      ),
      true,
      "The source is locked and Name is not while resolving",
    );
    await js("(() => { window.fetch = window.realFetch; window.release(); })()");

    // Added: one item whose two playlists, "Run" and "Run (2)", are top-level rows of their own.
    await wait(
      "!document.querySelector('dialog') && [...document.querySelectorAll('[role=treeitem]')].some((row) => row.textContent.startsWith('Run (2)'))",
    );
    const levels = await js(
      "[...document.querySelectorAll('[role=treeitem]')].filter((row) => /^Run( \\(2\\))?$/.test(row.textContent)).map((row) => row.getAttribute('aria-level'))",
    );
    assert.deepEqual(levels, ["1", "1"], "Each playlist is a top-level row");

    await js(
      "[...document.querySelectorAll('[role=group]')].find((pane) => pane.ariaLabel === 'two-runs')?.querySelector('[aria-label=\"Close this pane\"]').click()",
    );
    await post("/api/plugins/remove", { target: "two-runs" });
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
};
