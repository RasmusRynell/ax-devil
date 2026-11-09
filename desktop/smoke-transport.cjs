// The smoke test's transport scenario, on the video the video scenario opened: what the browser does with the seek
// bar's track, and with controls that have faded out.
const assert = require("node:assert/strict");
const { center, hideChrome, page, settled, showChrome, until } = require("./smoke-kit.cjs");

const SHOWN = `Number.parseInt(document.querySelector("input[aria-label=Frame]")?.value ?? "")`;
const TRANSPORT = "document.querySelector('[data-keeps-chrome=transport]')";

/**
 * A press on the seek bar's track goes to the frame under the pointer: the ends of the track are the first and the last
 * frame, and the middle of it is the middle of the video.
 */
async function seekTrack(window) {
  const { js } = page(window);
  await showChrome(window);
  const bar = await settled(window, center("transport:seek"));
  const last = await js(`Number(document.querySelector('[data-cursor-id="transport:seek"]').max)`);
  const near = Math.max(2, Math.round(last * 0.05));
  for (const [what, x, frame, allowed] of [
    ["last", bar.right - 1, last, 0],
    ["first", bar.left + 1, 0, 0],
    ["middle", (bar.left + bar.right) / 2, Math.round(last / 2), near],
  ]) {
    for (const type of ["mouseMove", "mouseDown", "mouseUp"])
      window.webContents.sendInputEvent({
        type,
        x: Math.round(x),
        y: bar.y,
        button: "left",
        clickCount: 1,
      });
    await until(
      async () => Math.abs((await js(SHOWN)) - frame) <= allowed,
      `a press at the ${what} of the track shows frame ${frame} of ${last}`,
    );
  }
}

/**
 * A faded transport takes no click, so the video under it does; the keyboard focus on one of its controls shows it
 * again, with its clicks.
 */
async function fadedControls(window) {
  const { js } = page(window);
  await hideChrome(window);
  assert.equal(
    await js(`getComputedStyle(${TRANSPORT}).pointerEvents`),
    "none",
    "A faded transport takes no pointer",
  );
  const overVideo = await js(`(() => {
    const t = ${TRANSPORT}.getBoundingClientRect();
    const hit = document.elementFromPoint(t.left + t.width / 2, t.top + t.height / 2);
    return !!hit && !${TRANSPORT}.contains(hit) && !!hit.closest('[data-chrome-idle]');
  })()`);
  assert.ok(overVideo, "A click where a faded control lies reaches the video");
  // A key press first, so the focus a script gives counts as the keyboard's.
  for (const type of ["keyDown", "keyUp"])
    window.webContents.sendInputEvent({ type, keyCode: "Shift" });
  await js(`${TRANSPORT}.querySelector('button').focus()`);
  await until(
    () =>
      js(
        `Number(getComputedStyle(${TRANSPORT}).opacity) === 1 && getComputedStyle(${TRANSPORT}).pointerEvents !== 'none'`,
      ),
    "the transport is shown while a control has the keyboard focus",
  );
  await js("document.activeElement.blur()");
}

module.exports.transport = async (window) => {
  await seekTrack(window);
  await fadedControls(window);
};
