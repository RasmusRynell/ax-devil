// The smoke test's video scenario: add a video the way a user does, then check the picture on the real canvas.
const { pressShortcut } = require("./shortcut-input.cjs");
const { dialog, nativeTheme } = require("electron");
const fs = require("node:fs/promises");
const assert = require("node:assert/strict");
const path = require("node:path");
const {
  SCREENSHOTS,
  SMOKE_ROOT,
  openAddVideo,
  delay,
  frames,
  goToFrame,
  page,
  setView,
  until,
} = require("./smoke-kit.cjs");

const { headerMenu } = require("./smoke-header-menu.cjs");
const { transport } = require("./smoke-transport.cjs");

const FIXTURE = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
const SHOWN = `Number.parseInt(document.querySelector("input[aria-label=Frame]")?.value ?? "")`;
/** A pixel this bright is the picture: the field around it is near-black. */
const LIT = 40;

/**
 * Pauses a video that just opened and steps back to its first frame: an ordinary open starts playing once that frame is
 * shown, a clip that ended first paused itself, and an entry shown again opens paused where it was left, which the
 * short wait lets pass.
 */
module.exports.pauseAtStart = async (window) => {
  const { js } = page(window);
  for (let i = 0; i < 30; i++) {
    if (await js(`!!document.querySelector('[aria-label=Pause]') || ${SHOWN} > 0`)) break;
    await delay(100);
  }
  await goToFrame(window, 0);
};

/**
 * Runs in the page: how the open frame info sits in its room, as readable problems, and whether it is cut. Every line in
 * sight lies whole inside the HUD's padding box, the last one of a cut HUD is the standalone `…` row and no heading
 * is last in sight, the first row left out really has no room, and the HUD stays inside the video pane.
 */
function infoHud() {
  const hud = document.querySelector('[aria-label$="frame info"]');
  const pane = hud?.closest("[data-chrome-idle]");
  if (!pane) return { problems: ["the frame info is not in a video pane"], cut: false };
  const problems = [];
  const style = getComputedStyle(hud);
  const box = hud.getBoundingClientRect();
  const room = {
    top: box.top + parseFloat(style.paddingTop),
    bottom: box.bottom - parseFloat(style.paddingBottom),
  };
  const lines = [...hud.querySelectorAll("[data-line]")];
  const seen = lines.filter((line) => getComputedStyle(line).visibility !== "hidden");
  const left = lines.filter((line) => !seen.includes(line));
  for (const line of seen) {
    const { top, bottom } = line.getBoundingClientRect();
    if (top < room.top - 1 || bottom > room.bottom + 1)
      problems.push(
        `half cut: "${line.textContent}" at ${top}-${bottom} in ${room.top}-${room.bottom}`,
      );
  }
  const last = seen.at(-1);
  if (!last) problems.push("no line in sight");
  else if (last.dataset.line === "heading")
    problems.push(`heading last in sight: "${last.textContent}"`);
  if (left.length > 0 && last?.textContent !== "…")
    problems.push(`cut, but the last line in sight is "${last?.textContent}"`);
  if (left.length === 0 && seen.some((line) => line.textContent === "…"))
    problems.push("a row of … with nothing left out");
  const next = left.find((line) => line.dataset.line === "row");
  if (next && next.getBoundingClientRect().bottom <= room.bottom - 1)
    problems.push(`left out with room: "${next.textContent}"`);
  const into = pane.getBoundingClientRect();
  if (
    box.left < into.left - 1 ||
    box.right > into.right + 1 ||
    box.top < into.top - 1 ||
    box.bottom > into.bottom + 1
  )
    problems.push("the frame info is outside the video pane");
  return { problems, cut: left.length > 0 };
}

/**
 * Runs in the page: what the pane header says beside the title, and whether the whole title is still there, in a pane
 * made as wide as the measured title and details need plus a margin (the details must sit beside the title) and in one
 * as wide as the title and half the details need (they cannot, so the header keeps the whole title and gives the
 * details up). Measured, not fixed, so it holds whatever the font. The pane's own width is put back after every reading.
 */
function headerDetails() {
  const details = document.querySelector("[data-header-details]");
  const row = details?.parentElement;
  if (!row) return null;
  const header = row.parentElement;
  const pane = header.parentElement;
  const titleBox = row.firstElementChild;
  const title = titleBox.firstElementChild;
  const chrome = header.clientWidth - row.clientWidth;
  // The title's own width, even where the pane now cuts it.
  const titleWidth = title.scrollWidth + titleBox.offsetWidth - title.offsetWidth;
  const MARGIN = 4;
  const width = pane.style.width;
  const read = (room) => {
    pane.style.width = `${chrome + room}px`;
    return {
      beside: details.getBoundingClientRect().top < row.getBoundingClientRect().bottom - 1,
      titleWhole: title.scrollWidth <= title.clientWidth,
    };
  };
  try {
    return {
      text: details.textContent,
      wide: read(titleWidth + details.offsetWidth + MARGIN),
      narrow: read(titleWidth + details.offsetWidth / 2),
    };
  } finally {
    pane.style.width = width;
  }
}

/** Asserts the pane header gives the title the room first, and the details only the room that is left. */
async function expectHeaderPriority(window, what) {
  const found = await page(window).js(`(${headerDetails.toString()})()`);
  assert.deepEqual(
    [found?.wide.beside, found?.wide.titleWhole],
    [true, true],
    `The details sit beside the title ${what}`,
  );
  assert.deepEqual(
    [found.narrow.beside, found.narrow.titleWhole],
    [false, true],
    `The title wins the header ${what}`,
  );
  return found.text;
}

/**
 * Waits for the frame info to settle after a change of view (a missed measurement stays missed, so the last check is
 * the answer), then asserts it fits. Returns whether it is cut.
 */
async function expectInfoHud(window, what) {
  let found;
  for (let i = 0; i < 30; i++) {
    found = await page(window).js(`(${infoHud.toString()})()`);
    if (found.problems.length === 0) return found.cut;
    await delay(100);
  }
  assert.deepEqual(found.problems, [], `The frame info must fit ${what}`);
}

/**
 * Holds the playback socket's handshake, so a viewer stays in its first state, opening, until `release`. The workspace
 * session's own socket is another path and goes through.
 */
function holdPlayback(window) {
  const requests = window.webContents.session.webRequest;
  const held = [];
  requests.onBeforeRequest(
    { urls: ["ws://*/api/playback", "wss://*/api/playback"] },
    (_details, answer) => held.push(answer),
  );
  return {
    release() {
      requests.onBeforeRequest(null);
      for (const answer of held.splice(0)) answer({});
    },
  };
}

/**
 * Runs in the page: how the loading indicator sits in its pane, as readable problems. It covers the whole pane, so
 * nothing under it takes a click, and its arc and message lie inside it.
 */
function loadingCover() {
  const cover = document.querySelector("[data-loading]");
  if (!cover) return ["there is no loading indicator"];
  const problems = [];
  const pane = cover.parentElement.getBoundingClientRect();
  const box = cover.getBoundingClientRect();
  for (const side of ["left", "top", "right", "bottom"])
    if (Math.abs(box[side] - pane[side]) > 1)
      problems.push(`the cover's ${side} is off the pane's`);
  const hit = document.elementFromPoint((pane.left + pane.right) / 2, (pane.top + pane.bottom) / 2);
  if (!cover.contains(hit)) problems.push("something under the cover takes the pointer");
  const arc = cover.querySelector("svg").getBoundingClientRect();
  for (const part of [arc, ...[...cover.children].slice(1).map((e) => e.getBoundingClientRect())])
    if (part.left < box.left - 1 || part.right > box.right + 1 || part.bottom > box.bottom + 1)
      problems.push("the arc or its message is outside the cover");
  return problems;
}

/** Checks the loading indicator and takes a screenshot of it in both themes, at the normal view and the smallest at 200%. */
async function loadingShots(window) {
  const { js, wait } = page(window);
  await fs.mkdir(SCREENSHOTS, { recursive: true });
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    /** @type {[number[], number, string][]} */
    const views = [
      [[1100, 720], 1, ""],
      [[640, 480], 2, "-200"],
    ];
    for (const [size, zoom, suffix] of views) {
      // The sidebar fills the smallest window, so it is closed there as in the frame info check.
      if (zoom === 2) await js("document.querySelector('[aria-label=Workspace]').click()");
      await setView(window, size, zoom);
      assert.deepEqual(
        await js(`(${loadingCover.toString()})()`),
        [],
        `The loading indicator must cover the pane at ${size.join("x")} and ${zoom * 100}% zoom in the ${theme} theme`,
      );
      await fs.writeFile(
        path.join(SCREENSHOTS, `video-loading-${theme}${suffix}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
      if (zoom === 2) {
        await setView(window, [1100, 720], 1);
        await js("document.querySelector('[aria-label=Workspace]').click()");
      }
    }
  }
}

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  window.setSize(1100, 720);
  window.webContents.setZoomFactor(1);
  // Add Video opens its dialog; the native file dialog behind Browse answers with the fixture, and Add sends the command.
  const original = dialog.showOpenDialog.bind(dialog);
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [FIXTURE] });
  const opening = holdPlayback(window);
  try {
    await js(
      "!document.querySelector('[aria-label=\"Workspace items\"], [data-cursor-id=\"sidebar:add-video\"]') && document.querySelector('[aria-label=Workspace]').click()",
    );
    await frames({ js });
    await openAddVideo(window);
    await wait("document.querySelector('dialog[open] [aria-label=\"Choose video file\"]')");
    await js("document.querySelector('dialog[open] [aria-label=\"Choose video file\"]').click()");
    const add =
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Add')";
    await wait(`${add}.disabled === false`);
    await js(`${add}.click()`);
    // The picture is not there yet: the loading indicator covers the pane, in the smallest window and at 200% too.
    await wait("document.querySelector('[data-loading]')");
    await loadingShots(window);
    opening.release();
    await wait(`${SHOWN} >= 0`);
    assert.equal(await js("document.querySelector('[data-loading]')"), null);
  } finally {
    opening.release();
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
  await module.exports.pauseAtStart(window);
  let canvas = await box("canvas");
  const centre = () => ({
    x: (canvas.left + canvas.right) / 2,
    y: (canvas.top + canvas.bottom) / 2,
  });
  let middle = centre();
  const black = brightness(await snap(), middle.x, middle.y);
  for (let step = 1; step <= 12; step++) {
    await js("document.querySelector('[aria-label=\"Forward 1 frame\"]').click()");
    await wait(`${SHOWN} === ${step}`);
  }
  // The split area can still be taking back a scroll bar's width from the 200% view above, so the picture is read
  // from a screenshot the canvas and the pointer anchor hold still across.
  const placed = () =>
    Promise.all([box("canvas"), box('[data-cursor-id^="frame:"]')]).then(JSON.stringify);
  let shot;
  let anchor;
  for (let tries = 0; ; tries++) {
    const before = await placed();
    shot = await snap();
    const after = await placed();
    if (after === before) {
      [canvas, anchor] = JSON.parse(after);
      middle = centre();
      break;
    }
    assert.ok(tries < 20, "The video pane kept changing its layout");
  }
  const grey = brightness(shot, middle.x, middle.y);
  assert.ok(black < 16, `Frame 0 of the video is black, got ${black}`);
  assert.ok(grey > 40, `Frame 12 must be a visible grey, got ${grey}: the canvas is still black`);

  // The pointer anchor lies over the picture itself, not over the bars the canvas keeps around it.
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
  await transport(window);
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
    pressShortcut(window, keyCode);
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
    const info = "document.querySelector('[aria-label$=\"frame info\"]')";
    await key("I");
    await wait(info);
    await shoot(`video-info-${theme}.png`);
    assert.equal(await expectInfoHud(window, `the 1100x720 window in the ${theme} theme`), false);
    assert.match(
      await expectHeaderPriority(window, `in the 1100x720 window in the ${theme} theme`),
      /^\d+×\d+ · [\d.]+ fps · \d+:\d\d$/,
      "The header tells the video",
    );
    // The frame info stays open while the window shrinks to its minimum and zooms to 200%, with the sidebar closed.
    await js("document.querySelector('[aria-label=Workspace]').click()");
    for (const zoom of [1, 2]) {
      await setView(window, [640, 480], zoom);
      const cut = await expectInfoHud(
        window,
        `the 640x480 window at ${zoom * 100}% zoom in the ${theme} theme`,
      );
      // Only the smallest view cannot hold every row, which is the case the cut is for.
      if (zoom === 2)
        assert.equal(cut, true, "The frame info is cut at 200% zoom in the smallest window");
      await shoot(`video-info-min-${theme}${zoom === 1 ? "" : "-200"}.png`);
      await expectHeaderPriority(
        window,
        `in the 640x480 window at ${zoom * 100}% zoom in the ${theme} theme`,
      );
    }
    await setView(window, [1100, 720], 1);
    await js("document.querySelector('[aria-label=Workspace]').click()");
    assert.equal(
      await expectInfoHud(window, `the 1100x720 window again in the ${theme} theme`),
      false,
    );
    await key("I");
    await wait(`!${info}`);
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
      process.platform === "darwin" ? "Export video⌘E" : "Export videoCtrl+E",
      process.platform === "darwin"
        ? "Toggle Media Tools Panel⌘B"
        : "Toggle Media Tools PanelCtrl+B",
      "Toggle Lane FullscreenF",
      "Add BookmarkM",
      "Reset Zoom0",
    ];
    assert.deepEqual(items.slice(0, 5), expected, "The lane menu lists the lane's commands");
    await shoot(`video-lane-menu-${theme}.png`);
    await key("Escape");
    await wait("!document.querySelector('[role=menu]')");
    await headerMenu(window, { js, wait, key, theme });
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
