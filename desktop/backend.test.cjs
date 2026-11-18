const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { mock, test } = require("node:test");
const { createBackend } = require("./backend.cjs");

// A backend process that prints nothing until told to, and ends when its stdin closes or SIGINT or SIGKILL reaches
// it; a `stubborn` one ignores the closed stdin.
function fakeProcess({ stubborn = false } = {}) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.exitCode = null;
  child.signalCode = null;
  child.signals = [];
  child.stdin = new PassThrough();
  const end = (signal) =>
    setImmediate(() => {
      child.signalCode = signal;
      child.emit("exit", null, signal);
      child.emit("close", null, signal);
    });
  if (!stubborn) child.stdin.on("finish", () => end("SIGTERM"));
  child.kill = (signal) => {
    child.signals.push(signal);
    if (signal === "SIGINT" || signal === "SIGKILL") end(signal);
    return true;
  };
  return child;
}

function backendOf(child) {
  return createBackend({
    port: "0",
    executable: "ax-devil",
    spawner: () => child,
    onUnexpectedExit: () => {},
  });
}

void test("stopping while the backend is starting closes its stdin and waits for it to end", async () => {
  const child = fakeProcess();
  const backend = backendOf(child);
  const started = assert.rejects(backend.ensure(), /stopped during startup/);

  await backend.stop();

  assert.deepEqual(child.signals, []);
  assert.ok(child.signalCode, "stop resolves only once the process has ended");
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

void test("a backend that is ready is stopped by closing its stdin", async () => {
  const child = fakeProcess();
  const backend = backendOf(child);
  assert.equal(backend.owned(), false);
  const ensured = backend.ensure();
  child.stdout.write("ax-devil: http://127.0.0.1:7330\n");
  assert.equal(await ensured, "http://127.0.0.1:7330");
  assert.equal(backend.owned(), true);

  await backend.stop();

  assert.deepEqual(child.signals, []);
});

void test("a backend that ignores its closed stdin gets SIGINT, then SIGKILL", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const child = fakeProcess({ stubborn: true });
    child.kill = (signal) => {
      child.signals.push(signal);
      return true;
    };
    const backend = backendOf(child);
    const ensured = backend.ensure();
    child.stdout.write("ax-devil: http://127.0.0.1:7330\n");
    await ensured;
    const stopped = backend.stop();
    assert.equal(backend.stop(), stopped);
    mock.timers.tick(3000);
    assert.deepEqual(child.signals, ["SIGINT"]);
    mock.timers.tick(3000);
    assert.deepEqual(child.signals, ["SIGINT", "SIGKILL"]);
    child.emit("exit", null, "SIGKILL");
    await stopped;
  } finally {
    mock.timers.reset();
  }
});

void test(
  "cleanup after an early backend exit preserves the scenario failure and never signals again",
  { timeout: 2000 },
  async () => {
    const { startProcess } = require("./backend.cjs");
    const child = fakeProcess({ stubborn: true });
    const pending = startProcess("backend", [], {}, () => {}, { spawner: () => child });
    child.stdout.write("ax-devil: http://127.0.0.1:7330\n");
    const served = await pending;
    child.exitCode = 23;
    child.emit("exit", 23, null);
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      await assert.rejects(async () => {
        try {
          throw new Error("scenario failed");
        } finally {
          await served.lifecycle.stop();
        }
      }, /scenario failed/);
      mock.timers.tick(60000);
      assert.deepEqual(child.signals, []);
    } finally {
      mock.timers.reset();
    }
  },
);

void test("repeated child stop sends one interrupt and arms one escalation", async () => {
  const { observe } = require("./child.cjs");
  const child = fakeProcess({ stubborn: true });
  child.kill = (signal) => child.signals.push(signal);
  const owned = observe(child);
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const first = owned.stop();
    assert.equal(owned.stop(), first);
    mock.timers.tick(3000);
    assert.deepEqual(child.signals, ["SIGINT", "SIGKILL"]);
    child.emit("exit", 0, null);
    await first;
    await owned.stop();
    mock.timers.tick(60000);
    assert.deepEqual(child.signals, ["SIGINT", "SIGKILL"]);
  } finally {
    mock.timers.reset();
  }
});

void test(
  "cleanup after startup exit or spawn failure settles without signalling",
  { timeout: 1000 },
  async () => {
    for (const event of ["exit", "error"]) {
      const child = fakeProcess();
      const backend = backendOf(child);
      const started = assert.rejects(backend.ensure());
      child.emit(event, event === "error" ? new Error("spawn failed") : 0);
      if (event === "exit") child.emit("close", 0, null);
      await started;
      await backend.stop();
      assert.deepEqual(child.signals, []);
    }
  },
);

void test("startup failure forwards the user-facing reason even when exit precedes the last stdout data", async () => {
  const child = fakeProcess();
  const starting = backendOf(child).ensure();
  child.stderr.write("camera password: MUST-NOT-REACH-DIALOG\n");
  child.exitCode = 1;
  child.emit("exit", 1, null);
  child.stdout.write(
    `ax-devil-error: ${JSON.stringify("Invalid storage directory: storage.cache_dir. Change settings.json or CACHE.")}\n`,
  );
  child.emit("close", 1, null);
  await assert.rejects(starting, {
    message: "Invalid storage directory: storage.cache_dir. Change settings.json or CACHE.",
  });
});

void test(
  "an exit before delayed startup output rejects once without waiting for close",
  { timeout: 1000 },
  async () => {
    for (const reason of [undefined, "Repair the configuration"]) {
      const child = fakeProcess();
      let unexpected = 0;
      const backend = createBackend({
        port: "0",
        executable: "ax-devil",
        spawner: () => child,
        onUnexpectedExit: () => {
          unexpected += 1;
        },
      });
      const starting = backend.ensure();
      const rejected = assert.rejects(starting, {
        message: reason ?? "Backend stopped during startup",
      });
      child.exitCode = 1;
      child.emit("exit", 1, null);
      child.stdout.write("ax-devil: http://127.0.0.1:7330\n");
      if (reason) child.stdout.write(`ax-devil-error: ${JSON.stringify(reason)}\n`);
      await rejected;
      child.emit("close", 1, null);
      assert.equal(unexpected, 0, "startup refusal does not also report an unexpected exit");
      await backend.stop();
    }
  },
);
