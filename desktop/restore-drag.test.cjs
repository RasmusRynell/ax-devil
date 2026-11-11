const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
let pointer;
const original = Module._load;
Module._load = (name, ...args) =>
  name === "electron"
    ? { screen: { getCursorScreenPoint: () => pointer } }
    : original(name, ...args);
const { restoreDrag, supportsRestoreDrag } = require("./restore-drag.cjs");
Module._load = original;

void test("Wayland keeps native dragging; only a known X11 backend exposes the fallback", () => {
  for (const [ozone, env, offered] of [
    ["wayland", { DISPLAY: ":1" }, false],
    ["", { DISPLAY: ":1", WAYLAND_DISPLAY: "wayland-1", XDG_SESSION_TYPE: "wayland" }, false],
    ["auto", { DISPLAY: ":1" }, false],
    ["x11", { WAYLAND_DISPLAY: "wayland-1" }, true],
    ["", { DISPLAY: ":1", XDG_SESSION_TYPE: "x11" }, true],
  ]) {
    const capability = supportsRestoreDrag("linux", ozone, env);
    let bridge;
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8"), {
      process: { argv: capability ? ["--ax-devil-restore-drag"] : [] },
      require: () => ({
        contextBridge: {
          exposeInMainWorld: (_name, value) => {
            bridge = value;
          },
        },
      }),
    });
    assert.equal(typeof bridge.restoreDrag === "function", offered, JSON.stringify({ ozone, env }));
  }
});

void test("ending or replacing a gesture retires its pending unmaximize callback", () => {
  const window = new EventEmitter();
  const writes = [];
  Object.assign(window, {
    isMaximized: () => true,
    isDestroyed: () => false,
    getBounds: () => ({ x: 0, y: 0, width: 1280, height: 1000 }),
    getNormalBounds: () => ({ x: 100, y: 100, width: 800, height: 600 }),
    webContents: Object.assign(new EventEmitter(), { getZoomFactor: () => 1 }),
    unmaximize: () => {}, // Explicit gate: the test releases the WM event below.
    setBounds: (bounds) => writes.push(bounds),
  });
  const pending = () => {
    pointer = { x: 640, y: 15 };
    restoreDrag(window, "start", 30);
    pointer = { x: 642, y: 17 };
    restoreDrag(window, "move");
    return window.listeners("unmaximize")[0];
  };
  for (const exit of [
    "end",
    "replace",
    "blur",
    "closed",
    "navigation",
    "render-process-gone",
    "destroyed",
  ]) {
    const stale = pending();
    assert.equal(typeof stale, "function");
    if (exit === "replace") restoreDrag(window, "start", 30);
    else if (exit === "end") restoreDrag(window, "end");
    else if (exit === "navigation") {
      window.webContents.emit("did-start-navigation", {}, "about:blank", false, false);
      assert.equal(window.listenerCount("unmaximize"), 1, "subframe keeps the current gesture");
      window.webContents.emit("did-start-navigation", {}, "about:blank", false, true);
    } else if (exit === "render-process-gone" || exit === "destroyed")
      window.webContents.emit(exit);
    else window.emit(exit);
    assert.equal(window.listenerCount("unmaximize"), 0, exit);
    for (const name of ["did-start-navigation", "render-process-gone", "destroyed"])
      assert.equal(
        window.webContents.listenerCount(name),
        exit === "replace" ? 1 : 0,
        `${exit}: ${name} retired`,
      );
    window.emit("unmaximize");
    stale(); // Even a callback already queued before cancellation must not write bounds.
    assert.deepEqual(writes, [], exit);
    restoreDrag(window, "end");
  }
  pending();
  window.emit("unmaximize");
  assert.deepEqual(writes, [{ x: 242, y: 2, width: 800, height: 600 }]);
  restoreDrag(window, "end");
});
