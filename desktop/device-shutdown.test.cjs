const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createDeviceValues, DEVICE_DRAIN_MS } = require("./device.cjs");
const { createShutdown } = require("./shutdown.cjs");
const { BACKEND_STOP_MS } = require("./child.cjs");
const { LAUNCH_TEARDOWN_MS, KILL_AFTER_MS, PACKAGE_EXIT_MS } = require("./smoke-budget.cjs");

for (const stall of ["sync", "rename", "late failure"]) {
  void test(
    `quit is bounded with a hung ${stall}, and late settlement is inert`,
    { timeout: 5000 },
    async (t) => {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-shutdown-"));
      t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
      const file = path.join(folder, "device.json");
      const original = { "ui.theme": "auto" };
      fs.writeFileSync(file, JSON.stringify(original), { mode: 0o600 });
      const owner = createDeviceValues(file);
      const announcements = [];
      owner.subscribe((snapshot) => announcements.push(snapshot));
      let release;
      const blocked = new Promise((resolve) => {
        release = resolve;
      });
      let entered;
      const started = new Promise((resolve) => {
        entered = resolve;
      });
      let finished;
      const settled = new Promise((resolve) => {
        finished = resolve;
      });
      if (stall === "rename") {
        const rename = fs.promises.rename;
        t.mock.method(fs.promises, "rename", async (...args) => {
          entered();
          await blocked;
          await rename(...args);
          finished();
        });
      } else {
        const open = fs.promises.open;
        const unlink = fs.promises.unlink;
        t.mock.method(fs.promises, "open", async (...args) => {
          const handle = await open(...args);
          t.mock.method(handle, "sync", async () => {
            entered();
            await blocked;
            if (stall === "late failure") throw new Error("late disk failure");
          });
          return handle;
        });
        t.mock.method(fs.promises, "unlink", async (...args) => {
          await unlink(...args);
          finished();
        });
      }
      const save = owner.write({ "ui.theme": "dark" });
      const refused = assert.rejects(save, /deadline/);
      await started;
      t.mock.timers.enable({ apis: ["setTimeout"] });
      const calls = [];
      const quit = createShutdown({
        device: owner,
        backend: {
          stop: async () => {
            calls.push("backend");
          },
        },
        exit: () => calls.push("exit"),
      });
      const quitting = quit();
      t.mock.timers.tick(DEVICE_DRAIN_MS - 1);
      assert.deepEqual(calls, []);
      t.mock.timers.tick(1);
      await quitting;
      await refused;
      assert.deepEqual(calls, ["backend", "exit"]);
      assert.equal(quit(), quitting);
      assert.equal(DEVICE_DRAIN_MS + BACKEND_STOP_MS, 8000);
      assert.ok(
        DEVICE_DRAIN_MS + BACKEND_STOP_MS <
          Math.min(LAUNCH_TEARDOWN_MS, KILL_AFTER_MS, PACKAGE_EXIT_MS),
      );
      // A restart ignores the private staged file and reads the complete target.
      assert.deepEqual(createDeviceValues(file).read().values, original);
      release();
      await settled;
      await new Promise(setImmediate);
      assert.deepEqual(calls, ["backend", "exit"]);
      assert.deepEqual(announcements, []);
      assert.deepEqual(owner.read(), { values: original, sequence: 0 });
      assert.deepEqual(
        createDeviceValues(file).read().values,
        stall === "rename" ? { "ui.theme": "dark" } : original,
      );
    },
  );
}
