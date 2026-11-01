// The smoke test's overlay scenarios on the real canvas: what the picture's marks are made of must not depend on the app
// theme or the page zoom, and what the pointer shows (outline, chip, inspector) must sit on the marks that are drawn.
// Run on frame 0 of MINI-02 (one lane, DET), whose object 3 is the box 31,22 16x24 of its 64x48 picture.
const assert = require("node:assert/strict");
const { capture, colourful, delay, moveTo, page, setView } = require("./smoke-kit.cjs");

const IMAGE = { width: 64, height: 48 };
const OBJECT = { x: 31, y: 22, width: 16, height: 24 };

/** The picture and the lane's name tag in CSS pixels, as the page lays them out. */
const GEOMETRY = `(() => {
  const rect = (element) => element.getBoundingClientRect().toJSON();
  const grid = document.querySelector('canvas').parentElement.parentElement;
  return {
    picture: rect(document.querySelector('[data-cursor-id^="frame:"]')),
    tag: rect(grid.querySelector(':scope > div.absolute:not(.inset-0)')),
  };
})()`;

/** Where object 3 is in CSS pixels, from the picture's rectangle. */
const boxOf = ({ left, top, width, height }) => ({
  left: left + (OBJECT.x / IMAGE.width) * width,
  right: left + ((OBJECT.x + OBJECT.width) / IMAGE.width) * width,
  top: top + (OBJECT.y / IMAGE.height) * height,
  bottom: top + ((OBJECT.y + OBJECT.height) / IMAGE.height) * height,
});

module.exports.overlays = async (window, { setTheme }) => {
  const { js, wait } = page(window);
  const away = () => moveTo(window, 4, 4);
  const geometry = () => js(GEOMETRY);

  // A theme or zoom is shown once the marks were drawn for it: the picture holds colour and stops changing.
  const drawn = async () => {
    let last = null;
    for (let i = 0; i < 50; i++) {
      const shot = await capture(window);
      const { picture } = await geometry();
      const count = colourCount(shot, picture);
      if (count > 0 && count === last?.count) return { ...shot, picture, count };
      last = { count };
      await delay(100);
    }
    throw new Error("The overlay marks did not settle on the picture");
  };
  const colourCount = (shot, picture) => {
    let count = 0;
    forPixels(shot, picture, (x, y) => colourful(shot, x, y) && count++);
    return count;
  };
  const forPixels = ({ scale }, picture, visit) => {
    for (let y = Math.ceil(picture.top * scale); y < Math.floor(picture.bottom * scale); y++)
      for (let x = Math.ceil(picture.left * scale); x < Math.floor(picture.right * scale); x++)
        visit(x, y);
  };

  // The sidebar is closed: at 200% zoom a window of this size is narrow enough for it to fill the window.
  await js("document.querySelector('[aria-label=Workspace]').click()");
  await away();

  // (a) The app theme does not touch the marks: the picture's pixels are the same in light and dark. The name tag is
  // interface, so it is left out.
  const pictures = {};
  for (const theme of ["light", "dark"]) {
    await setTheme(theme);
    pictures[theme] = await drawn();
  }
  const { tag } = await geometry();
  const [light, dark] = [pictures.light, pictures.dark];
  assert.equal(light.scale, dark.scale);
  let differing = 0;
  forPixels(light, light.picture, (x, y) => {
    const [px, py] = [x / light.scale, y / light.scale];
    if (px >= tag.left - 1 && px <= tag.right + 1 && py >= tag.top - 1 && py <= tag.bottom + 1)
      return;
    const at = (y * light.width + x) * 4;
    for (let channel = 0; channel < 3; channel++)
      if (Math.abs(light.bitmap[at + channel] - dark.bitmap[at + channel]) > 2) return differing++;
  });
  assert.equal(
    differing,
    0,
    "The picture's marks must look the same in the light and the dark theme",
  );
  await setTheme("light");

  // (b) A label keeps its size on screen when the page is zoomed: the id label above object 3 is as many device pixels
  // tall at 100% as at 200%. It is the run of coloured pixels above the box's top-left corner, taken a few pixels
  // inside it where neither the box's side nor the label's text is.
  const labelHeight = ({ scale, picture, ...shot }) => {
    const { left, top } = boxOf(picture);
    const [x, y] = [Math.round(left * scale), Math.round(top * scale)];
    let tallest = 0;
    for (const dx of [2, 3]) {
      let run = 0;
      for (let at = y - 60; at < y + 30; at++) {
        run = colourful({ scale, ...shot }, x + dx, at) ? run + 1 : 0;
        tallest = Math.max(tallest, run);
      }
    }
    return tallest;
  };
  const heights = [];
  for (const zoom of [1, 2]) {
    await setView(window, [1100, 720], zoom);
    heights.push(labelHeight(await drawn()));
  }
  assert.ok(
    heights[0] >= 10 && heights[0] <= 30,
    `The label must be measured: ${heights[0]} device pixels`,
  );
  assert.ok(
    Math.abs(heights[0] - heights[1]) <= 1,
    `A label must be as tall at 200% zoom as at 100%: ${heights[1]} against ${heights[0]} device pixels`,
  );
  await setView(window, [1100, 720], 1);

  // (c) After a wheel zoom the outline of the hovered object lies on the box that is drawn. The box's edges are read
  // from the pixels with the pointer away, the outline's from the page once the pointer is over the object.
  const middle = (box) => [(box.left + box.right) / 2, (box.top + box.bottom) / 2];
  let shot = await drawn();
  const [wheelX, wheelY] = middle(boxOf(shot.picture));
  await moveTo(window, wheelX, wheelY);
  for (let notch = 0; notch < 3; notch++)
    window.webContents.sendInputEvent({
      type: "mouseWheel",
      x: Math.round(wheelX),
      y: Math.round(wheelY),
      deltaX: 0,
      deltaY: 100,
    });
  const before = shot.picture.width;
  const canvasSize = () =>
    js(
      "(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return [r.width, r.height]; })()",
    );
  const canvasBefore = await canvasSize();
  await wait(
    `document.querySelector('[data-cursor-id^="frame:"]').getBoundingClientRect().width > ${before} * 1.4`,
  );
  await away();
  // The zoomed picture's anchors reach past the cell; that must neither scroll the page nor shrink the canvas.
  assert.deepEqual(
    await js(
      "(() => { const main = document.querySelector('main'); return [main.scrollWidth - main.clientWidth, main.scrollHeight - main.clientHeight]; })()",
    ),
    [0, 0],
    "After a wheel zoom the page must not scroll in either direction",
  );
  assert.deepEqual(
    await canvasSize(),
    canvasBefore,
    "A wheel zoom must not change the canvas's size",
  );
  await wait("!document.querySelector('[data-outline]')");
  shot = await drawn();
  const expected = boxOf(shot.picture);
  const edges = (box) => {
    const { scale } = shot;
    const [midX, midY] = middle(box).map((value) => Math.round(value * scale));
    const near = (from, to, along) => {
      for (let at = from; at !== to; at += Math.sign(to - from)) if (along(at)) return at;
      return Number.NaN;
    };
    const slack = 10;
    const left = near(Math.round(box.left * scale) - slack, midX, (x) => colourful(shot, x, midY));
    const right = near(Math.round(box.right * scale) + slack, midX, (x) =>
      colourful(shot, x, midY),
    );
    const highX = Math.round((box.left + 0.8 * (box.right - box.left)) * scale);
    const top = near(Math.round(box.top * scale) - slack, midY, (y) => colourful(shot, highX, y));
    return { left: left / scale, right: (right + 1) / scale, top: top / scale };
  };
  const drawnEdges = edges(expected);
  const [hoverX, hoverY] = middle(expected);
  await moveTo(window, hoverX, hoverY);
  await wait(
    "document.querySelector('[data-outline=hover]') && document.querySelector('[role=tooltip]')",
  );
  const outline = await js(
    "(() => { const r = document.querySelector('[data-outline=hover]').getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top }; })()",
  );
  // The chip never covers the object's own id label, which sits above the box's top edge.
  const chipTop = await js("document.querySelector('[role=tooltip]').getBoundingClientRect().top");
  assert.ok(
    chipTop >= outline.top - 1,
    `The hover chip at ${chipTop} must not reach above the box's top edge at ${outline.top}`,
  );
  for (const edge of ["left", "right", "top"])
    assert.ok(
      Math.abs(drawnEdges[edge] - outline[edge]) <= 2.5,
      `After a wheel zoom the hover outline's ${edge} edge is at ${outline[edge]}, the drawn box's at ${drawnEdges[edge]}`,
    );

  // (d) The chip names the object; a click opens the inspector with its fields; Escape closes it.
  assert.match(await js("document.querySelector('[role=tooltip]').textContent"), /^3/);
  for (const type of ["mouseDown", "mouseUp"])
    window.webContents.sendInputEvent({
      type,
      x: Math.round(hoverX),
      y: Math.round(hoverY),
      button: "left",
      clickCount: 1,
    });
  await wait("document.querySelector('aside[aria-label=Inspector] [aria-busy=false]')");
  const inspector = () =>
    js("document.querySelector('aside[aria-label=Inspector]')?.innerText ?? ''");
  const text = await inspector();
  assert.match(text, /^3 person\nscore 0\.51\n/);
  assert.match(
    text,
    /Classification\n(?:.*\n)*?score\n0\.51\n/,
    "The inspector must list the object's fields",
  );
  assert.doesNotMatch(text, /Loading|Not in this frame|not available/);
  assert.ok(
    await js("!!document.querySelector('[data-outline=selected]')"),
    "The selected object has an outline",
  );
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
  await wait(
    "!document.querySelector('aside[aria-label=Inspector]') && !document.querySelector('[data-outline=selected]')",
  );
  return { point: [hoverX, hoverY] };
};
