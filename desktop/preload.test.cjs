const assert = require("node:assert/strict");
const Module = require("node:module");
const { test } = require("node:test");

// The preload takes Electron's renderer modules at load time, which a plain Node test does not have; they are replaced
// only while it loads.
const preload = require.resolve("./preload.cjs");

/** The names the preload exposes to a page opened with `argv`'s shell arguments. */
function exposed(...argv) {
  let bridge;
  const electron = {
    contextBridge: { exposeInMainWorld: (_name, api) => (bridge = api) },
    ipcRenderer: {},
    webFrame: {},
  };
  const load = Module._load;
  const original = process.argv;
  Module._load = (request, ...rest) => (request === "electron" ? electron : load(request, ...rest));
  process.argv = [...original, ...argv];
  try {
    delete require.cache[preload];
    require(preload);
  } finally {
    Module._load = load;
    process.argv = original;
    delete require.cache[preload];
  }
  return Object.keys(bridge);
}

void test("a window has only the methods its feature offers", () => {
  const local = exposed();
  assert.ok(local.includes("pickVideo") && local.includes("connect") && local.includes("openLogs"));
  assert.ok(!local.includes("disconnect") && !local.includes("restart"));
  assert.ok(exposed("--ax-devil-restartable").includes("restart"));

  const remote = exposed("--ax-devil-remote");
  assert.ok(remote.includes("disconnect") && remote.includes("close"));
  for (const method of ["pickVideo", "pickFolder", "connect", "openLogs", "restart"])
    assert.ok(!remote.includes(method), method);
});
