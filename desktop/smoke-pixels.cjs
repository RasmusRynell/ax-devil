// The smoke test's pixel scenario: what the viewer's real renderer puts on screen for an 8x8 picture of 4x4-pixel black
// and white blocks, on a second backend of its own started like `ax-devil local --video V`. The picture is enlarged
// many times, so its block edges show whether neighbouring source pixels are blended, and the bars around it show the
// colour of the empty field.
const assert = require("node:assert/strict");
const path = require("node:path");
const { SHOWN, SMOKE_ROOT, capture, page } = require("./smoke-kit.cjs");
const { inBackendWindow } = require("./smoke-twice.cjs");

const CHECKER = path.join(SMOKE_ROOT, "fixtures/video/checker_h264_8x8.mp4");
/** The canvas token, #0e0f10: the empty field behind and around a picture. */
const FIELD = [0x0e, 0x0f, 0x10];
/** How far a decoded black or white may be off 0 or 255. */
const SLACK = 24;

module.exports.pixels = () =>
  inBackendWindow(["--video", CHECKER], async (window) => {
    const { js, wait } = page(window);
    await wait(`${SHOWN} >= 0`);
    const shot = await capture(window);
    const box = await js(
      `(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()`,
    );
    const at = (x, y) => (Math.round(y * shot.scale) * shot.width + Math.round(x * shot.scale)) * 4;
    // The bitmap is blue, green, red, alpha.
    const rgb = (x, y) => [
      shot.bitmap[at(x, y) + 2],
      shot.bitmap[at(x, y) + 1],
      shot.bitmap[at(x, y)],
    ];
    const side = Math.min(box.width, box.height);
    const left = box.left + (box.width - side) / 2;
    const top = box.top + (box.height - side) / 2;
    assert.ok(side > 64, `The 8x8 picture is shown enlarged, ${side} pixels wide`);

    // Beside the square picture the canvas is the empty field.
    const bar =
      box.width > box.height
        ? [box.left + 2, box.top + box.height / 2]
        : [box.left + box.width / 2, box.top + 2];
    assert.deepEqual(rgb(...bar), FIELD, "The empty field is the canvas colour");

    // Along a line through the top row of blocks, every pixel is black or white but the one or two at the edge: a
    // blended edge would pass through greys over a stretch as wide as a source pixel.
    const y = top + side / 4;
    const line = [];
    for (let x = Math.ceil(left) + 1; x < left + side - 1; x++) line.push(rgb(x, y)[0]);
    const dark = line.filter((value) => value <= SLACK).length;
    const bright = line.filter((value) => value >= 255 - SLACK).length;
    assert.ok(
      dark > 0 && bright > 0,
      `Both block colours are on the line (${dark} dark, ${bright} bright)`,
    );
    assert.ok(
      line.length - dark - bright <= 2,
      `Enlarged pixels stay hard: ${line.length - dark - bright} of ${line.length} pixels on the line are grey`,
    );
  });
