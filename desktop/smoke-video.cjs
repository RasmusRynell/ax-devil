// The smoke test's video scenario: add a video the way a user does, then check the picture on the real canvas.
const { dialog } = require("electron");
const assert = require("node:assert/strict");
const path = require("node:path");
const { click, frames, page } = require("./smoke-kit.cjs");

const FIXTURE = path.resolve(__dirname, "../fixtures/video/long_gop_h264.mp4");
const SHOWN = `Number.parseInt(document.querySelector("output[aria-label=Frame]")?.textContent ?? "")`;
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
};
