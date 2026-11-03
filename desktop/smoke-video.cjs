// The smoke test's video scenario: add a video the way a user does, then check the picture on the real canvas.
const { dialog, nativeTheme } = require("electron");
const fs = require("node:fs/promises");
const assert = require("node:assert/strict");
const path = require("node:path");
const { SCREENSHOTS, SMOKE_ROOT, click, frames, page, until } = require("./smoke-kit.cjs");

const FIXTURE = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
const SHOWN = `Number.parseInt(document.querySelector("input[aria-label=Frame]")?.value ?? "")`;
/** A pixel this bright is the picture: the field around it is near-black. */
const LIT = 40;

module.exports.video = async (window) => {
  const { js, wait } = page(window);
  window.setSize(1100, 720);
  window.webContents.setZoomFactor(1);
  // The native file dialog answers with the fixture; the page sends its Session the Add Video command as usual.
  const original = dialog.showOpenDialog.bind(dialog);
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [FIXTURE] });
  try {
    await js(
      "document.querySelector('aside h2')?.textContent !== 'Workspace' && document.querySelector('[aria-label=Workspace]').click()",
    );
    await wait("document.querySelector('[data-cursor-id=\"sidebar:add-video\"]')");
    await frames({ js });
    await click(window, "sidebar:add-video");
    await wait(`${SHOWN} === 0`);
  } finally {
    dialog.showOpenDialog = original;
  }

  // Frame N of this video is flat with luma 7 N, so frame 0 is black and frame 12 a visible grey.
  const snap = async () => {
    await frames({ js });
    const image = await window.webContents.capturePage();
    const { width } = image.getSize();
    return { bitmap: image.toBitmap(), width, scale: width / (await js("innerWidth")) };
  };
  const brightness = ({ bitmap, width, scale }, x, y) => {
    const at = (Math.round(y * scale) * width + Math.round(x * scale)) * 4;
    return Math.max(bitmap[at], bitmap[at + 1], bitmap[at + 2]);
  };
  const box = (selector) =>
    js(
      `(() => { const r = document.querySelector('${selector}').getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; })()`,
    );
  await js("document.querySelector('[aria-label=Pause]')?.click()");
  const canvas = await box("canvas");
  const middle = { x: (canvas.left + canvas.right) / 2, y: (canvas.top + canvas.bottom) / 2 };
  const black = brightness(await snap(), middle.x, middle.y);
  for (let step = 1; step <= 12; step++) {
    await js("document.querySelector('[aria-label=\"Forward 1 frame\"]').click()");
    await wait(`${SHOWN} === ${step}`);
  }
  const shot = await snap();
  const grey = brightness(shot, middle.x, middle.y);
  assert.ok(black < 16, `Frame 0 of the video is black, got ${black}`);
  assert.ok(grey > 40, `Frame 12 must be a visible grey, got ${grey}: the canvas is still black`);

  // The pointer anchor lies over the picture itself, not over the bars the canvas keeps around it.
  const anchor = await box('[data-cursor-id^="frame:"]');
  const edge = (from, to, at) => {
    const lit = (value) => brightness(shot, ...at(value)) > LIT;
    let value = from;
    while (value !== to && !lit(value)) value += Math.sign(to - from);
    return value;
  };
  const left = edge(Math.floor(canvas.left), Math.ceil(canvas.right), (x) => [x, middle.y]);
  const right =
    edge(Math.ceil(canvas.right) - 1, Math.floor(canvas.left), (x) => [x, middle.y]) + 1;
  const top = edge(Math.floor(canvas.top), Math.ceil(canvas.bottom), (y) => [middle.x, y]);
  for (const [what, drawn, anchored] of [
    ["left", left, anchor.left],
    ["right", right, anchor.right],
    ["top", top, anchor.top],
  ])
    assert.ok(
      Math.abs(drawn - anchored) <= 2,
      `The pointer anchor's ${what} edge is at ${anchored}, the picture's at ${drawn}`,
    );
  assert.ok(
    Math.abs(anchor.top - canvas.top - (canvas.bottom - anchor.bottom)) <= 2,
    "The pointer anchor must be centred like the picture",
  );
  await fullscreen(window);
};

// Lane fullscreen takes the viewer pane with its transport, Escape leaves it and Space plays; application fullscreen
// (F11) is the window's and survives Escape. The fullscreen steps run where the OS completes the transition: the
// macOS CI runner enters it (the pane fills the screen) but never leaves it on Escape, so macOS skips them and
// keeps the menu and Space checks, which do not depend on it.
async function fullscreen(window) {
  const native = process.platform !== "darwin";
  const { js, wait } = page(window);
  const key = async (keyCode) => {
    for (const type of ["keyDown", "keyUp"]) window.webContents.sendInputEvent({ type, keyCode });
    await frames({ js });
  };
  const fullscreenPane = `document.fullscreenElement?.querySelector('canvas') && document.fullscreenElement.querySelector('[data-keeps-chrome]')`;
  const playing = "document.querySelector('[aria-label=Pause]')";
  const shoot = async (name) => {
    await frames({ js });
    await fs.mkdir(SCREENSHOTS, { recursive: true });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    if (native) {
      await key("F");
      await wait(fullscreenPane);
      await shoot(`video-lane-fullscreen-${theme}.png`);
      await key("Escape");
      await wait("!document.fullscreenElement");
    }
    // A right click on the picture offers the lane's commands, each with its key.
    const { x, y } = await js(
      "(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 3) }; })()",
    );
    for (const type of ["mouseMove", "mouseDown", "mouseUp"])
      window.webContents.sendInputEvent({ type, x, y, button: "right", clickCount: 1 });
    await wait("document.querySelector('[role=menu] [role=menuitem]')");
    const items = await js(
      "[...document.querySelectorAll('[role=menu] [role=menuitem]')].map((item) => item.textContent)",
    );
    const expected = [
      "Export VideoCtrl+E",
      "Media Tools PanelCtrl+B",
      "Toggle Lane FullscreenF",
      "Reset Zoom0",
    ];
    assert.deepEqual(items.slice(0, 4), expected, "The lane menu lists the lane's commands");
    await shoot(`video-lane-menu-${theme}.png`);
    await key("Escape");
    await wait("!document.querySelector('[role=menu]')");
  }
  if (native)
    assert.equal(
      await js(
        "document.activeElement === document.querySelector('[data-chrome-idle]').closest('[tabindex]')",
      ),
      true,
      "Leaving lane fullscreen puts the keyboard back on the viewer",
    );
  await key("Space");
  await wait(playing);
  await key("Space");
  await wait("!document.querySelector('[aria-label=Pause]')");

  if (!native) return;
  await key("F11");
  await until(() => window.isFullScreen() || undefined, "the window to fill the screen");
  await key("F");
  await wait(fullscreenPane);
  await key("Escape");
  await wait("!document.fullscreenElement");
  assert.equal(window.isFullScreen(), true, "Escape leaves lane fullscreen, not the window's");
  await key("F11");
  await until(() => !window.isFullScreen() || undefined, "the window to leave fullscreen");
}
