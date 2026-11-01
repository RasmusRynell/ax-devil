// The smoke test's repeated-overlay scenario: one overlay file given twice for a video becomes two lanes, named after
// the file and numbered, and both draw. Only the command line can give a video an overlay, so this runs a second
// backend of its own, started like `ax-devil local --video V --overlay F --overlay F`, in a window of its own.
const { BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { startProcess } = require("./backend.cjs");
const {
  SCREENSHOTS,
  SHOWN,
  colourCount,
  delay,
  goToFrame,
  laneCells,
  page,
  until,
} = require("./smoke-kit.cjs");

const VIDEO = path.resolve(__dirname, "../fixtures/video/long_gop_h264.mp4");
const OVERLAY = path.resolve(__dirname, "../fixtures/datasets/mot/train/MINI-01-DPM/gt/gt.txt");

module.exports.twice = async () => {
  const served = await startProcess(
    ["local", "--video", VIDEO, "--overlay", OVERLAY, "--overlay", OVERLAY],
    { PORT: "0" },
    () => {},
  );
  const window = new BrowserWindow({
    width: 1100,
    height: 720,
    frame: false,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      preload: path.resolve(__dirname, "preload.cjs"),
    },
  });
  try {
    const { js, wait } = page(window);
    await window.loadURL(served.origin);
    // The first run's Quick Setup comes first when this backend's page has not seen it.
    await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done')?.click()",
    );
    // The video the command line added is open at once; its two lanes are one file under two names.
    await wait(`${SHOWN} >= 0`);
    await goToFrame(window, 0);
    const cells = await until(async () => {
      const found = await laneCells(window);
      return found.length === 2 && found;
    }, "two lanes");
    assert.deepEqual(
      cells.map((cell) => cell.label),
      ["gt.txt", "gt.txt (2)"],
    );
    // Both lanes show their boxes of frame 0.
    for (const cell of cells) {
      const deadline = Date.now() + 5000;
      while ((await colourCount(window, cell)) === 0 && Date.now() < deadline) await delay(100);
      assert.ok(await colourCount(window, cell), `${cell.label}: no boxes on frame 0`);
    }
    await fs.writeFile(
      path.join(SCREENSHOTS, "overlay-twice.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
  } finally {
    window.destroy();
    const exited = new Promise((resolve) => served.child.once("exit", resolve));
    served.child.kill("SIGINT");
    const killer = setTimeout(() => served.child.kill("SIGKILL"), 3000);
    await exited;
    clearTimeout(killer);
  }
};
