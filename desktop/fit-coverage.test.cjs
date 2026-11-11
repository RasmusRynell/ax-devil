// Guard: every frontend file that opens a dialog names the smoke scenario that fit-checks it at the window minimum and
// 200% zoom, or the reason it has none. A new dialog fails this test until it is listed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const src = path.join(__dirname, "..", "frontend", "src");

/** File under `frontend/src` -> the smoke script that fit-checks it. */
const CHECKED = {
  "export/ExportDialog.tsx": "smoke-export.cjs",
  "kit/Gallery.tsx": "smoke-gallery.cjs",
  "lanes/LaneCatalogActions.tsx": "smoke-panels.cjs",
  "shell/InformationDialog.tsx": "smoke-items.cjs",
  "shell/AboutDialog.tsx": "smoke-shell.cjs",
  "shell/AddLiveStreamDialog.tsx": "smoke-live.cjs",
  "shell/AddPlaylistDialog.tsx": "smoke-playlist.cjs",
  "shell/AddVideoDialog.tsx": "smoke-remote.cjs",
  "shell/InformationDialog.tsx": "smoke-playlist.cjs",
  "shell/ConnectDialog.tsx": "smoke-shell.cjs",
  "shell/QuickSetupDialog.tsx": "smoke.cjs",
  "shell/SettingsDialog.tsx": "smoke-shell.cjs",
  // The questions it asks are short sentences in the choice dialog the gallery checks.
  "shell/Shell.tsx": "smoke-gallery.cjs",
  "shell/ShortcutsDialog.tsx": "smoke-shell.cjs",
  "shell/UncaughtErrors.tsx": "smoke-shell.cjs",
};

/** File under `frontend/src` -> why no smoke scenario checks it. */
const EXEMPT = {
  "bookmarks/RenameBookmark.tsx": "one text field, the shape of the Connect dialog's password page",
  "session/Notice.tsx":
    "the server password dialog is one masked field, the shape of the Connect dialog's password page",
};

function sources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? sources(file) : [file];
  });
}

void test("every dialog is fit-checked by a smoke scenario or exempt with a reason", () => {
  const opening = sources(src)
    .filter((file) => file.endsWith(".tsx") && !file.endsWith(".test.tsx"))
    .filter((file) => /<(Choice)?Dialog(Frame)?\b/.test(fs.readFileSync(file, "utf8")))
    .map((file) => path.relative(src, file).split(path.sep).join("/"))
    .filter((file) => !file.startsWith("kit/") || file === "kit/Gallery.tsx");
  const known = new Set([...Object.keys(CHECKED), ...Object.keys(EXEMPT)]);
  assert.deepEqual(
    opening.filter((file) => !known.has(file)),
    [],
    "add the new dialog to CHECKED (with the smoke scenario that fit-checks it) or to EXEMPT (with a reason)",
  );
  assert.deepEqual(
    [...known].filter((file) => !opening.includes(file)),
    [],
    "these files no longer open a dialog: remove them from the lists",
  );
  for (const scenario of new Set(Object.values(CHECKED)))
    assert.ok(fs.existsSync(path.join(__dirname, scenario)), `${scenario} does not exist`);
});
