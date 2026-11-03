// What the desktop smoke test and its remote-server scenario share: driving a window's page, finding controls by
// their cursor ids, and a few checks on the page itself.
const path = require("node:path");

// Fixtures and screenshots live under the repository, or under `AX_DEVIL_SMOKE_ROOT` when the app is packaged.
const SMOKE_ROOT = process.env.AX_DEVIL_SMOKE_ROOT || path.resolve(__dirname, "..");
const SCREENSHOTS = path.join(SMOKE_ROOT, "artifacts/screenshots");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Runs expressions in a window's page; `wait` polls one until it is truthy. Results are copied out of the page, so
// an expression must not evaluate to a DOM node.
const page = (window) => {
  const js = (expression) => window.webContents.executeJavaScript(expression);
  const wait = async (expression) => {
    for (let i = 0; i < 100; i++) {
      if (await js(`Boolean(${expression})`)) return;
      await delay(100);
    }
    throw new Error(`Timed out: ${expression}\n${await js("document.body.innerText")}`);
  };
  return { js, wait };
};

// Waits until the window has drawn the state it is in.
const frames = (driver) =>
  driver.js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");

// Sizes a window and zooms its page, then waits until the page reports the geometry the fit checks assume: the window's
// size divided by the zoom, in CSS pixels. Both changes reach the page some time after the calls return, and below 640
// CSS pixels the layout changes (the sidebar fills the window), so measuring earlier sees an intermediate layout.
async function setView(window, [width, height], zoom) {
  const { js } = page(window);
  window.setSize(width, height);
  window.webContents.setZoomFactor(zoom);
  const expected = [width / zoom, height / zoom];
  let seen = [];
  for (let i = 0; i < 100; i++) {
    seen = await js("[innerWidth, innerHeight]");
    if (seen.every((size, axis) => Math.abs(size - expected[axis]) <= 1)) return frames({ js });
    await delay(100);
  }
  throw new Error(
    `Timed out: the page must be ${expected.join("x")} CSS pixels at ${width}x${height} and ${zoom * 100}% zoom, but is ${seen.join("x")}`,
  );
}

// Records the pointer events the page sees, for the report when the controls do not fade.
const recordPointer = (window) =>
  page(window).js(`(() => {
    if (!window.smokePointer) {
      window.smokePointer = [];
      for (const type of ["pointerover", "pointerenter", "pointerleave", "pointermove"])
        document.addEventListener(type, (e) => {
          const t = e.target;
          const where = t.closest?.("[data-chrome-idle]") ? "video" : t.closest?.("[data-keeps-chrome]") ? "controls" : t.localName;
          window.smokePointer.push(type + "@" + e.clientX + "," + e.clientY + ":" + where);
        }, true);
    }
    window.smokePointer.length = 0;
  })()`);

// What decides whether the controls over the video are shown, as one line for a failure report.
async function chromeState(win) {
  const state = await page(win).js(`(() => {
    const t = document.querySelector("[data-keeps-chrome=transport]");
    const pane = document.querySelector("[data-chrome-idle]")?.closest("[tabindex]");
    const style = t && getComputedStyle(t);
    const tag = (e) => (e ? e.outerHTML.slice(0, 100) : null);
    return {
      visibility: document.visibilityState,
      pageFocused: document.hasFocus(),
      paneChrome: pane?.dataset.chrome ?? "unset",
      opacity: style?.opacity,
      pointerEvents: style?.pointerEvents,
      transition: style ? style.transitionProperty + " " + style.transitionDuration + " " + style.transitionDelay : null,
      animations: t?.getAnimations().map((a) => a.playState + "@" + a.currentTime) ?? [],
      focusInTransport: tag(t?.querySelector(":focus-visible")),
      active: tag(document.activeElement),
      atCorner: tag(document.elementFromPoint(4, 4)),
      playing: !!document.querySelector("[aria-label=Pause]"),
      frame: document.querySelector("input[aria-label=Frame]")?.value,
      reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      page: [innerWidth, innerHeight, devicePixelRatio],
      pointer: window.smokePointer ?? [],
    };
  })()`);
  const shell = {
    focused: win.isFocused(),
    visible: win.isVisible(),
    bounds: win.getBounds(),
    zoom: win.webContents.getZoomFactor(),
  };
  return JSON.stringify({ ...state, shell });
}

// The transport lies over the bottom of the picture until the pointer has rested: waits until it has faded out, first
// crossing the video with the pointer and leaving it when the controls were never hidden.
async function hideChrome(window) {
  const { js } = page(window);
  const opacity = () =>
    js(
      "(() => { const t = document.querySelector('[data-keeps-chrome=transport]'); return t ? Number(getComputedStyle(t).opacity) : 0; })()",
    );
  if ((await opacity()) === 0) return;
  const video = await js(
    "(() => { const r = document.querySelector('[data-chrome-idle]').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()",
  );
  await recordPointer(window);
  // Moves sent together reach the page as one, and a pointer that never entered the video cannot leave it: the
  // pointer leaves only once the page has seen it on the video.
  await moveTo(window, video.x, video.y);
  try {
    await until(
      () =>
        js(
          "document.querySelector('[data-chrome-idle]').closest('[tabindex]').dataset.chrome === 'shown'",
        ),
      "the pointer reaches the video",
    );
    await moveTo(window, 4, 4);
    await until(async () => (await opacity()) === 0, "the transport fades out");
  } catch (error) {
    error.message += `\n${await chromeState(window)}`;
    throw error;
  }
}

// A faded transport takes no pointer, so a check that hit-tests its controls brings it back first: the pointer rests
// over the video, and the transport is shown until it has rested for the idle time. Does nothing when no picture is on
// screen: with no video open, or with a side panel filling a small window over it.
async function showChrome(window) {
  const { js } = page(window);
  const video = await js(
    "(() => { const r = document.querySelector('[data-chrome-idle]')?.getBoundingClientRect(); return r && r.width > 0 && r.height > 0 ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; })()",
  );
  if (!video) return;
  const shown = () =>
    js(
      "(() => { const t = document.querySelector('[data-keeps-chrome=transport]'); return document.querySelector('[data-chrome-idle]').closest('[data-chrome]')?.dataset.chrome === 'shown' && !!t && Number(getComputedStyle(t).opacity) === 1 && getComputedStyle(t).pointerEvents !== 'none'; })()",
    );
  await moveTo(window, video.x, video.y);
  await until(shown, "the transport is shown");
  // Resting over the transport holds the controls shown, so the idle timer cannot hide them during the check.
  const controls = await js(
    "(() => { const r = document.querySelector('[data-keeps-chrome=transport]').getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; })()",
  );
  if (!controls) return;
  await moveTo(window, controls.x, controls.y);
  await until(shown, "the transport is held shown");
  await frames({ js });
}

// The window as drawn: its pixels (device pixels, four bytes each) and the device pixels per CSS pixel. The picture
// is bare: the transport is faded out.
async function capture(window) {
  const { js } = page(window);
  await hideChrome(window);
  await frames({ js });
  const image = await window.webContents.capturePage();
  const { width, height } = image.getSize();
  return { bitmap: image.toBitmap(), width, height, scale: width / (await js("innerWidth")) };
}

// Whether a captured pixel carries colour: the grey picture, white text and dark name tags do not.
function colourful({ bitmap, width }, x, y) {
  const at = (y * width + x) * 4;
  return Math.max(...bitmap.subarray(at, at + 3)) - Math.min(...bitmap.subarray(at, at + 3)) >= 80;
}

// The frame number the transport shows.
const SHOWN = `Number.parseInt(document.querySelector("input[aria-label=Frame]")?.value ?? "")`;

// Pauses and steps the viewer to this frame.
async function goToFrame(window, target) {
  const { js, wait } = page(window);
  await js("document.querySelector('[aria-label=Pause]')?.click()");
  for (;;) {
    const at = await js(SHOWN);
    if (at === target) return;
    await js(
      `document.querySelector('[aria-label="${at < target ? "Forward" : "Back"} 1 frame"]').click()`,
    );
    await wait(`${SHOWN} !== ${at}`);
  }
}

// The lane cells on screen, from the name tags the grid places at each cell's corner: each is the part of the grid
// from its tag to the next tag across and down. The canvas's own box fills the grid and is not a tag.
const laneCells = (window) =>
  page(window).js(`(() => {
    const grid = document.querySelector('canvas').parentElement.parentElement;
    const tags = [...grid.querySelectorAll(':scope > div.absolute:not(.inset-0)')].map((tag) => ({
      label: tag.querySelector('span').textContent,
      ...tag.getBoundingClientRect().toJSON(),
    }));
    const end = grid.getBoundingClientRect();
    const next = (values, value, limit) => Math.min(limit, ...values.filter((other) => other > value + 1));
    return tags.map((tag) => ({
      label: tag.label,
      x: tag.left,
      y: tag.top,
      right: next(tags.map((other) => other.left), tag.left, end.right),
      bottom: next(tags.map((other) => other.top), tag.top, end.bottom),
    }));
  })()`);

// How many pixels of a lane cell carry colour.
async function colourCount(window, cell) {
  const shot = await capture(window);
  let count = 0;
  for (let y = Math.round(cell.y * shot.scale); y < Math.round(cell.bottom * shot.scale); y++)
    for (let x = Math.round(cell.x * shot.scale); x < Math.round(cell.right * shot.scale); x++)
      if (colourful(shot, x, y)) count++;
  return count;
}

// A real mouse pointer at a CSS pixel of the page (the window's coordinates are the page's times its zoom).
async function moveTo(window, x, y) {
  const zoom = window.webContents.getZoomFactor();
  window.webContents.sendInputEvent({
    type: "mouseMove",
    x: Math.round(x * zoom),
    y: Math.round(y * zoom),
  });
}

// A button in the sidebar, by its text.
const button = (label) =>
  `[...document.querySelectorAll('aside button')].find((b) => b.textContent === '${label}')`;
// The center and box of the element with this cursor id.
const center = (id) =>
  `(() => { const r = document.querySelector('[data-cursor-id="${id}"]').getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), left: r.left, right: r.right, top: r.top, bottom: r.bottom }; })()`;
// The name tag of a remote cursor.
const tag = (name) =>
  `[...document.querySelectorAll('[aria-hidden=true] span')].find((s) => s.textContent === '${name}')`;

// Collects what a window's page reports as a Content Security Policy violation: Chromium logs each one as a console error.
function watchCsp(window) {
  const violations = [];
  window.webContents.on("console-message", ({ message }) => {
    if (/Content Security Policy/i.test(message)) violations.push(message);
  });
  return violations;
}

// Reloads a window's page and waits until it has loaded again.
async function reload(window) {
  const loaded = new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  window.webContents.reload();
  await loaded;
}

// A real left click at the center of the element with this cursor id.
async function click(window, id) {
  const { x, y } = await page(window).js(center(id));
  for (const type of ["mouseMove", "mouseDown", "mouseUp"])
    window.webContents.sendInputEvent({ type, x, y, button: "left", clickCount: 1 });
}

// Polls until `find` returns something.
async function until(find, what) {
  for (let i = 0; i < 100; i++) {
    const found = await find();
    if (found) return found;
    await delay(100);
  }
  throw new Error(`Timed out: ${what}`);
}

module.exports = {
  SMOKE_ROOT,
  SCREENSHOTS,
  button,
  capture,
  center,
  colourCount,
  colourful,
  click,
  delay,
  frames,
  goToFrame,
  laneCells,
  moveTo,
  page,
  reload,
  SHOWN,
  setView,
  showChrome,
  tag,
  until,
  watchCsp,
};
