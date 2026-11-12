const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { mock, test } = require("node:test");
const { build } = require("./build-smoke-fixture.cjs");

void test("an expired build fails even when its owner subsequently exits zero", async () => {
  const child = Object.assign(new EventEmitter(), {
    pid: 12345,
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  const signals = [];
  const kill = mock.method(process, "kill", (...args) => signals.push(args));
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const result = build("fake", [], { spawner: () => child, limit: 10, report: () => {} });
    mock.timers.tick(10);
    child.emit("exit", 0, null);
    child.emit("close", 0);
    assert.equal(await result, 1);
    mock.timers.tick(60000);
    assert.deepEqual(signals, [[-12345, "SIGKILL"]]);
  } finally {
    kill.mock.restore();
    mock.timers.reset();
  }
});

void test(
  "a descendant retaining build output is reported without signalling the exited owner",
  { timeout: 5000 },
  async () => {
    const reports = [];
    let descendant;
    try {
      const code = await build(
        process.execPath,
        [
          "-e",
          `
      const { spawn } = require('node:child_process');
      const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 10000)'], { stdio: ['ignore', 1, 2] });
      console.log(child.pid);
      child.unref();
    `,
        ],
        {
          grace: 20,
          output: (chunk) => {
            descendant = Number(chunk.toString().trim());
          },
          report: (message) => reports.push(message),
        },
      );
      assert.equal(code, 1);
      assert.ok(descendant > 0);
      assert.equal(process.kill(descendant, 0), true);
      assert.match(
        reports.join("\n"),
        /descendant may still be running; no retired PID was signalled/,
      );
    } finally {
      if (descendant) process.kill(descendant, "SIGKILL");
    }
  },
);
