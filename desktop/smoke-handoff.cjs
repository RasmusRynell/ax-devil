// The smoke test's second-launch scenario: `ax-devil local --video V` run while the app is open opens V in a window of
// the running app, and a command the backend refuses says why.
const { BrowserWindow, app } = require("electron");
const assert = require("node:assert/strict");
const path = require("node:path");
const { backendExecutable } = require("./backend.cjs");
const { handOff } = require("./hand-off.cjs");
const { SMOKE_ROOT, page } = require("./smoke-kit.cjs");

// The folder the user typed the command in: not the one this process runs in, so a relative path proves the command
// was read there.
const TYPED_IN = path.join(SMOKE_ROOT, "fixtures/video");

async function handedOff(shell) {
  const before = new Set(BrowserWindow.getAllWindows());
  await shell.onLaunch({ command: ["local", "--video", "long_gop_h264.mp4"], cwd: TYPED_IN });
  const opened = BrowserWindow.getAllWindows().find((window) => !before.has(window));
  assert.ok(opened, "a second launch with a video opens a window");
  let origin;
  try {
    await page(opened).wait("document.body.innerText.includes('long_gop_h264.mp4')");
    origin = new URL(opened.webContents.getURL()).origin;
  } finally {
    opened.destroy();
  }

  await assert.rejects(
    handOff(backendExecutable(app), origin, ["open", "missing.ax-devil.workspace"], TYPED_IN),
    /missing\.ax-devil\.workspace/,
  );
}

module.exports = { handedOff };
