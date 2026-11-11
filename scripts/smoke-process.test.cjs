const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const events = require("node:events");
const { PassThrough } = require("node:stream");
const { test } = require("node:test");
const { supervise } = require("./smoke-process.cjs");
const { observe } = require("./owned-child.cjs");

const bounded = (promise) =>
  Promise.race([
    promise,
    new Promise((_, reject) => {
      const t = setTimeout(() => reject(new Error("probe timed out")), 3000);
      t.unref();
    }),
  ]);
const alive = (pid) => {
  try {
    // A reparented zombie has stopped and cannot receive signals or hold descriptors.
    if (
      process.platform === "linux" &&
      fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].startsWith("Z")
    )
      return false;
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (["ENOENT", "ESRCH"].includes(error.code)) return false;
    throw error;
  }
};
async function gone(pid) {
  await bounded(
    (async () => {
      while (alive(pid)) await new Promise((r) => setTimeout(r, 10));
    })(),
  );
}

for (const escaped of [false, true])
  void test(
    `wrapper exit with ${escaped ? "own-session" : "same-group"} survivor`,
    { timeout: 5000 },
    async () => {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "smoke-owner-"));
      const result = path.join(folder, "result");
      const signals = [];
      let wrapper;
      let descendant;
      try {
        const verdict = supervise(
          process.execPath,
          [
            "-e",
            `
      const { spawn } = require('node:child_process');
      const c = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: ${escaped}, stdio: ['ignore', 1, 2] });
      c.on('spawn', () => process.send(c.pid));
      process.on('message', () => process.exit(0));
    `,
          ],
          result,
          {
            limitMs: 2500,
            exitGraceMs: 100,
            spawner(command, args, options) {
              wrapper = spawn(command, args, {
                ...options,
                stdio: ["ignore", "pipe", "pipe", "ipc"],
              });
              return wrapper;
            },
            signalGroup(pid, signal) {
              signals.push([pid, signal]);
              process.kill(pid, signal);
            },
          },
        );
        [descendant] = await bounded(events.once(wrapper, "message"));
        fs.writeFileSync(result, "PASS");
        wrapper.send("release");
        const outcome = await bounded(verdict);
        assert.deepEqual(signals, [[-wrapper.pid, "SIGKILL"]]);
        if (escaped) {
          assert.equal(outcome.ok, false);
          assert.match(outcome.reason, /original process group.*another session/);
          assert.equal(alive(descendant), true, "the reported escape really was not reached");
        } else {
          assert.equal(outcome.ok, true, outcome.reason);
          await gone(descendant);
        }
      } finally {
        if (descendant && alive(descendant)) process.kill(descendant, "SIGKILL");
        if (wrapper && wrapper.exitCode === null && wrapper.signalCode === null)
          wrapper.kill("SIGKILL");
        if (descendant) await gone(descendant);
        fs.rmSync(folder, { recursive: true, force: true });
      }
    },
  );

function fake() {
  const child = new events.EventEmitter();
  Object.assign(child, {
    pid: 123,
    exitCode: null,
    signalCode: null,
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  child.end = () => {
    child.exitCode = 0;
    child.emit("exit", 0);
    child.emit("close", 0);
  };
  return child;
}
void test(
  "deadline is permanent even when its interrupt produces PASS and exit zero",
  { timeout: 3000 },
  async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "smoke-deadline-"));
    try {
      const result = path.join(folder, "result");
      const child = fake();
      const signals = [];
      const verdict = supervise("fake", [], result, {
        limitMs: 50,
        spawner: () => child,
        signalGroup(_pid, signal) {
          signals.push(signal);
          if (signal === "SIGTERM") {
            fs.writeFileSync(result, "PASS");
            child.end();
          }
        },
      });
      t.mock.timers.tick(50);
      assert.equal((await verdict).ok, false);
      t.mock.timers.tick(20000);
      assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);
    } finally {
      fs.rmSync(folder, { recursive: true, force: true });
    }
  },
);
void test(
  "stop twice interrupts and escalates once; early exit and spawn failure settle",
  { timeout: 3000 },
  async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const child = fake();
    const signals = [];
    child.kill = (signal) => {
      signals.push(signal);
      if (signal === "SIGKILL") child.end();
    };
    const owner = observe(child);
    const first = owner.stop();
    assert.equal(owner.stop(), first);
    t.mock.timers.tick(3000);
    await first;
    assert.deepEqual(signals, ["SIGINT", "SIGKILL"]);
    await owner.stop();
    const early = fake();
    early.exitCode = 0;
    await observe(early).stop();
    const failed = fake();
    const observed = observe(failed);
    failed.emit("error", new Error("spawn failed"));
    await observed.stop();
  },
);
void test("missing executable fails without waiting for pipes", { timeout: 3000 }, async () => {
  const result = await supervise("/nonexistent/ax-devil-test", [], "/nonexistent/result", {
    limitMs: 1000,
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /ENOENT/);
});

void test(
  "Bash run_group rejects PASS followed by forced stopping",
  { timeout: 5000 },
  async () => {
    const source = fs.readFileSync(path.join(__dirname, "smoke-packaged.sh"), "utf8");
    const functions = source.slice(
      source.indexOf("# Signals only"),
      source.indexOf("# Every group runs"),
    );
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "packaged-predicate-"));
    try {
      const probe = spawnSync(
        "bash",
        [
          "-c",
          `
      ${functions}
      os=win; logs="$1"; run=(bash -c 'echo $$ > "$1/pid"; echo PASS > "$AX_DEVIL_SMOKE_RESULT"; read -r _ < "$1/hold"' bash "$1"); flags=()
      native() { echo "$1"; }; fresh_home() { :; }
      export AX_DEVIL_SMOKE_RESULT
      mkfifo "$1/hold"
      sleep() { /bin/sleep 0.02; }
      taskkill() { kill -KILL -- "-$pid"; }
      run_group probe probe 100
    `,
          "probe",
          folder,
        ],
        { timeout: 3000, encoding: "utf8" },
      );
      assert.equal(probe.status, 1, probe.stdout + probe.stderr);
      assert.match(probe.stdout, /required forced stopping/);
      await gone(Number(fs.readFileSync(path.join(folder, "pid"), "utf8")));
    } finally {
      const pidFile = path.join(folder, "pid");
      if (fs.existsSync(pidFile)) {
        const pid = Number(fs.readFileSync(pidFile, "utf8"));
        if (alive(pid)) process.kill(pid, "SIGKILL");
        await gone(pid);
      }
      fs.rmSync(folder, { recursive: true, force: true });
    }
  },
);
