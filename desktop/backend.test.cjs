const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { mock, test } = require("node:test");
const { createBackend } = require("./backend.cjs");

// A backend process that prints nothing until told to, and ends when SIGINT or SIGKILL reaches it.
function fakeProcess() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.exitCode = null;
  child.signalCode = null;
  child.signals = [];
  child.kill = (signal) => {
    child.signals.push(signal);
    if (signal === "SIGINT" || signal === "SIGKILL")
      setImmediate(() => {
        child.signalCode = signal;
        child.emit("exit", null, signal);
      });
    return true;
  };
  return child;
}

function backendOf(child) {
  return createBackend({ port: "0", spawner: () => child, onUnexpectedExit: () => {} });
}

void test("stopping while the backend is starting signals it and waits for it to end", async () => {
  const child = fakeProcess();
  const backend = backendOf(child);
  const started = assert.rejects(backend.ensure(), /stopped during startup/);

  await backend.stop();

  assert.deepEqual(child.signals, ["SIGINT"]);
  assert.equal(child.signalCode, "SIGINT", "stop resolves only once the process has ended");
  await started;
});

void test("a backend that never says it is ready is killed when startup times out", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const child = fakeProcess();
    const started = assert.rejects(backendOf(child).ensure(), /timed out/);
    mock.timers.tick(10000);
    await started;
    assert.deepEqual(child.signals, ["SIGKILL"]);
  } finally {
    mock.timers.reset();
  }
});

void test("a backend that is ready is stopped with SIGINT", async () => {
  const child = fakeProcess();
  const backend = backendOf(child);
  const ensured = backend.ensure();
  child.stdout.write("ax-devil: http://127.0.0.1:7330\n");
  assert.equal(await ensured, "http://127.0.0.1:7330");

  await backend.stop();

  assert.deepEqual(child.signals, ["SIGINT"]);
});
