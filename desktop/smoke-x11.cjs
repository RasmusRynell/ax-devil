// Native input must enter X11, not webContents.sendInputEvent (which bypasses the window manager).
const { execFile, spawn } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");
const { SMOKE_ROOT, settled, frames, page, until } = require("./smoke-kit.cjs");
const execute = promisify(execFile);
const { shortcutInput } = require("./shortcut-input.cjs");
const { observe } = require("./child.cjs");

async function input(action, ...values) {
  if (process.env.AX_DEVIL_PRIVATE_X11 !== "1")
    throw new Error("Native window tests require scripts/isolated --window-manager");
  try {
    const result = await execute(
      "python3",
      [path.join(SMOKE_ROOT, "scripts/private-x11.py"), action, ...values.map(String)],
      { timeout: 5000, killSignal: "SIGKILL" },
    );
    return result.stdout;
  } catch (cause) {
    throw new Error(`Private X11 input ${action} failed (5 second limit)`, { cause });
  }
}

// Native key events use the same portable chord mapper as renderer smoke input.
async function pressShortcut(chord) {
  const { keyCode, modifiers } = shortcutInput(chord);
  await input("key", keyCode, ...modifiers);
}

async function point(window, selector) {
  const at = await settled(
    window,
    `(() => {
    const r = (${selector}).getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`,
  );
  const bounds = window.getContentBounds();
  const zoom = window.webContents.getZoomFactor();
  return { x: Math.round(bounds.x + at.x * zoom), y: Math.round(bounds.y + at.y * zoom) };
}

async function drag(window, from, to) {
  await input("move", from.x, from.y);
  try {
    await input("down");
    await frames(page(window));
    for (const fraction of [0.25, 0.5, 0.75, 1]) {
      await input(
        "move",
        Math.round(from.x + (to.x - from.x) * fraction),
        Math.round(from.y + (to.y - from.y) * fraction),
      );
      await frames(page(window));
    }
  } finally {
    await input("up");
  }
}
// Focus is observed at both boundaries before input, independent of Openbox's focus policy.
async function focus(window) {
  window.focus();
  await until(
    async () => window.isFocused() && (await page(window).js("document.hasFocus()")),
    "native and document focus before X11 input",
  );
}

// Probe each operation from a genuinely deactivated window, keeping the witness alive until it finishes.
async function withUnfocused(window, work) {
  // Start focused so an already inactive target cannot satisfy the probe before the witness maps.
  await focus(window);
  const other = spawn("python3", [path.join(SMOKE_ROOT, "scripts/private-x11.py"), "competitor"], {
    stdio: ["pipe", "inherit", "inherit"],
  });
  const lifecycle = observe(other);
  let failure;
  const closed = lifecycle.done.then(({ code, signal, error }) => {
    if (error || code || signal) failure = error ?? new Error(`exit ${code ?? signal}`);
  });
  try {
    await until(async () => {
      if (failure || other.exitCode !== null)
        throw new Error("Private X11 focus witness failed", { cause: failure });
      return !window.isFocused() && !(await page(window).js("document.hasFocus()"));
    }, "private X11 witness to deactivate native and document focus");
    return await work();
  } finally {
    other.stdin.end();
    let timer;
    try {
      await Promise.race([
        closed.then(() => {
          if (failure) throw new Error("Private X11 focus witness failed", { cause: failure });
        }),
        new Promise((_resolve, reject) => {
          timer = setTimeout(() => {
            reject(new Error("Private X11 focus witness cleanup timed out after 3 seconds"));
            lifecycle.signal("SIGKILL");
          }, 3000);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }
}
module.exports = { input, pressShortcut, point, drag, focus, withUnfocused };
