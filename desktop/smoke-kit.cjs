// What the desktop smoke test and its remote-server scenario share: driving a window's page, finding controls by
// their cursor ids, and a few checks on the page itself.
const path = require("node:path");

const SCREENSHOTS = path.resolve(__dirname, "../artifacts/screenshots");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Runs expressions in a window's page; `wait` polls one until it is true.
const page = (window) => {
  const js = (expression) => window.webContents.executeJavaScript(expression);
  const wait = async (expression) => {
    for (let i = 0; i < 100; i++) {
      if (await js(expression)) return;
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
  SCREENSHOTS,
  button,
  center,
  click,
  delay,
  frames,
  page,
  reload,
  setView,
  tag,
  until,
  watchCsp,
};
