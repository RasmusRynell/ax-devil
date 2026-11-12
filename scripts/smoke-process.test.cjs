const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const events = require("node:events");
const { PassThrough } = require("node:stream");
const { test, posixTest } = require("./test-platform.cjs").platformTests();
const { passed } = require("./smoke-verdict.cjs");
const { supervise } = require("./smoke-process.cjs");
const { observe, spawnOwned } = require("./child.cjs");

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
async function waitFor(check) {
  const deadline = Date.now() + 3000;
  while (!check()) {
    if (Date.now() >= deadline) throw new Error("probe timed out");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
async function gone(pid) {
  await waitFor(() => !alive(pid));
}
async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = events.once(child, "exit");
  child.kill("SIGKILL");
  await bounded(exited);
}

function audit(folder) {
  const log = path.join(folder, "signals");
  const preload = path.join(folder, "audit.cjs");
  fs.writeFileSync(
    preload,
    `
    const fs = require('node:fs');
    const kill = process.kill.bind(process);
    const alive = (pid) => { try { return kill(pid, 0); } catch (error) { if (error.code === 'ESRCH') return false; throw error; } };
    const log = (target, signal) => fs.appendFileSync(${JSON.stringify(log)}, \`\${JSON.stringify({ target, signal, caller: process.pid, leaderAlive: alive(target < 0 ? -target : process.pid) })}\\n\`);
    process.kill = (target, signal) => {
      if (signal !== 0) log(target, signal);
      return kill(target, signal);
    };
    const proto = require('node:child_process').ChildProcess.prototype;
    const childKill = proto.kill;
    proto.kill = function(signal) { log(this.pid, signal); return childKill.call(this, signal); };
  `,
  );
  return {
    env: { ...process.env, NODE_OPTIONS: `--require=${preload}` },
    read: () =>
      fs.existsSync(log)
        ? fs
            .readFileSync(log, "utf8")
            .trim()
            .split("\n")
            .map((line) => JSON.parse(line))
        : [],
  };
}
function assertSafe(signals) {
  for (const entry of signals) {
    if (entry.target < 0)
      assert.equal(
        entry.target,
        -entry.caller,
        `group signal must originate from its live leader: ${JSON.stringify(entry)}`,
      );
    assert.equal(
      entry.leaderAlive,
      true,
      `group leader must not be retired: ${JSON.stringify(entry)}`,
    );
  }
}
void posixTest(
  "normal completion audits the launcher too and rejects a post-exit group-signal mutant",
  { timeout: 10000 },
  async () => {
    for (const broken of [false, true]) {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "launcher-audit-"));
      const signals = audit(folder);
      let launcher;
      try {
        for (const file of [
          "smoke-process.cjs",
          "child.cjs",
          "group-leader.cjs",
          "smoke-verdict.cjs",
        ]) {
          let source = fs.readFileSync(path.join(__dirname, file), "utf8");
          if (broken && file === "smoke-process.cjs") {
            const guard = '    child.on("exit", () => {\n      if (finished) return;';
            assert.ok(source.includes(guard), "mutation must reach the normal exit callback");
            source = source.replace(guard, `${guard}\n      process.kill(-child.pid, "SIGKILL");`);
          }
          fs.writeFileSync(path.join(folder, file), source);
        }
        launcher = spawn(
          process.execPath,
          [
            path.join(folder, "smoke-process.cjs"),
            "2",
            path.join(folder, "result"),
            "normal-completion",
            process.execPath,
            "-e",
            'require("node:fs").writeFileSync(process.env.AX_DEVIL_SMOKE_RESULT, "PASS")',
          ],
          { env: signals.env, stdio: ["ignore", "pipe", "pipe"] },
        );
        launcher.stdout.resume();
        launcher.stderr.resume();
        const outcome = await bounded(events.once(launcher, "close"));
        const entries = signals.read();
        if (broken) {
          assert.ok(
            entries.some(
              (entry) => entry.caller === launcher.pid && entry.target < 0 && !entry.leaderAlive,
            ),
            "mutant must expose a launcher signal to a retired leader",
          );
          assert.throws(
            () => assertSafe(entries),
            /group signal must originate|group leader must not be retired/,
          );
        } else {
          assert.deepEqual(outcome, [0, null]);
          assert.ok(
            entries.some((entry) => entry.target < 0),
            "the live leader cleanup must be audited",
          );
          assertSafe(entries);
        }
      } finally {
        await stopChild(launcher);
        fs.rmSync(folder, { recursive: true, force: true });
      }
    }
  },
);

for (const escaped of [false, true])
  void posixTest(
    `wrapper exit with ${escaped ? "own-session" : "same-group"} survivor`,
    { timeout: 5000 },
    async () => {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "smoke-owner-"));
      const result = path.join(folder, "result");
      const release = path.join(folder, "release");
      const pidFile = path.join(folder, "pid");
      const signals = audit(folder);
      let descendant;
      let wrapper;
      try {
        const verdict = supervise(
          process.execPath,
          [
            "-e",
            `
        const fs = require('node:fs');
        const c = require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: ${escaped}, stdio: ['ignore', 1, 2] });
        fs.watch(${JSON.stringify(folder)}, () => { if (fs.existsSync(${JSON.stringify(release)})) process.exit(0); });
        fs.writeFileSync(${JSON.stringify(pidFile)}, String(c.pid));
      `,
          ],
          result,
          {
            group: "survivor-probe",
            limitMs: 2500,
            exitGraceMs: 100,
            spawner(command, args, options) {
              wrapper = spawnOwned(command, args, {
                ...options,
                env: { ...options.env, NODE_OPTIONS: signals.env.NODE_OPTIONS },
              });
              return wrapper;
            },
          },
        );
        await waitFor(() => fs.existsSync(pidFile));
        descendant = Number(fs.readFileSync(pidFile, "utf8"));
        fs.writeFileSync(result, "PASS");
        fs.writeFileSync(release, "go");
        const outcome = await bounded(verdict);
        assertSafe(signals.read());
        if (escaped) {
          assert.equal(outcome.ok, false);
          assert.match(outcome.reason, /survivor-probe.*escaped-session survivor/);
          assert.equal(alive(descendant), true);
          const before = signals.read();
          assert.equal(wrapper.kill("SIGKILL"), false);
          assert.deepEqual(signals.read(), before, "no delayed group signal after completion");
        } else {
          assert.equal(outcome.ok, true, outcome.reason);
          await gone(descendant);
        }
      } finally {
        await stopChild(wrapper);
        if (descendant && alive(descendant)) process.kill(descendant, "SIGKILL");
        if (descendant) await gone(descendant);
        fs.rmSync(folder, { recursive: true, force: true });
      }
    },
  );

void posixTest(
  "command signals are preserved and stubborn commands escalate through a live leader",
  { timeout: 10000 },
  async () => {
    for (const mode of ["signal", "escalation"]) {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "leader-status-"));
      const signals = audit(folder);
      let child;
      try {
        const ready = path.join(folder, "ready");
        child = spawnOwned(
          process.execPath,
          [
            "-e",
            mode === "signal"
              ? "process.kill(process.pid, 'SIGTERM')"
              : `process.on("SIGINT", () => {}); require("node:fs").writeFileSync(${JSON.stringify(ready)}, "ready"); setInterval(() => {}, 1000)`,
          ],
          { env: signals.env, stdio: "pipe" },
        );
        const done = bounded(events.once(child, "exit"));
        if (mode === "escalation") {
          const owner = observe(child, { graceMs: 20 });
          await waitFor(() => fs.existsSync(ready));
          await owner.stop();
        }
        const [code, signal] = await done;
        assert.equal(code, null);
        assert.equal(signal, mode === "signal" ? "SIGTERM" : "SIGKILL");
        assertSafe(signals.read());
      } finally {
        await stopChild(child);
        fs.rmSync(folder, { recursive: true, force: true });
      }
    }
  },
);

void posixTest(
  "launcher death disconnects IPC and removes the leader and its command",
  { timeout: 5000 },
  async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "leader-orphan-"));
    const signals = audit(folder);
    const info = path.join(folder, "info");
    let launcher;
    let ids;
    try {
      launcher = spawn(
        process.execPath,
        [
          "-e",
          `
      const { spawnOwned } = require(${JSON.stringify(require.resolve("./child.cjs"))});
      const child = spawnOwned(process.execPath, ['-e', 'require("node:fs").writeFileSync(${JSON.stringify(info)}, JSON.stringify({ leader: process.ppid, command: process.pid })); setInterval(() => {}, 1000)'], { env: process.env, stdio: 'pipe' });
      child.on('error', () => {});
    `,
        ],
        { env: signals.env, stdio: "ignore" },
      );
      await waitFor(() => fs.existsSync(info));
      ids = JSON.parse(fs.readFileSync(info, "utf8"));
      const exited = events.once(launcher, "exit");
      launcher.kill("SIGKILL");
      await bounded(exited);
      await gone(ids.leader);
      await gone(ids.command);
      assertSafe(signals.read());
    } finally {
      if (launcher && launcher.exitCode === null && launcher.signalCode === null)
        launcher.kill("SIGKILL");
      if (ids)
        for (const pid of Object.values(ids)) {
          if (alive(pid)) process.kill(pid, "SIGKILL");
          await gone(pid);
        }
      fs.rmSync(folder, { recursive: true, force: true });
    }
  },
);

void posixTest(
  "unexpected leader death fails and closes retained output without signalling its retired group",
  { timeout: 5000 },
  async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "leader-failure-"));
    const info = path.join(folder, "info");
    const signals = audit(folder);
    let launcher;
    let ids;
    try {
      launcher = spawn(
        process.execPath,
        [
          require.resolve("./smoke-process.cjs"),
          "2",
          path.join(folder, "result"),
          "owner-failure",
          process.execPath,
          "-e",
          `
      require('node:fs').writeFileSync(${JSON.stringify(info)}, JSON.stringify({ leader: process.ppid, command: process.pid }));
      setInterval(() => {}, 1000);
    `,
        ],
        { env: signals.env, stdio: ["ignore", "pipe", "pipe"] },
      );
      const exited = events.once(launcher, "close");
      let output = "";
      launcher.stdout.on("data", (chunk) => {
        output = `${output}${chunk}`;
      });
      launcher.stderr.on("data", (chunk) => {
        output = `${output}${chunk}`;
      });
      await waitFor(() => fs.existsSync(info));
      ids = JSON.parse(fs.readFileSync(info, "utf8"));
      process.kill(ids.leader, "SIGKILL");
      assert.deepEqual(await bounded(exited), [1, null], output);
      assert.match(output, /cleanup was not confirmed and descendants may remain/);
      assert.equal(alive(ids.command), true);
      assert.deepEqual(signals.read(), [], "a dead leader's numeric group is never targeted");
    } finally {
      if (ids) {
        if (alive(ids.command)) process.kill(ids.command, "SIGKILL");
        await gone(ids.command);
        await gone(ids.leader);
      }
      await stopChild(launcher);
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
      child.kill = (signal) => {
        signals.push(signal);
        if (signal === "SIGTERM") {
          fs.writeFileSync(result, "PASS");
          child.end();
        }
      };
      const verdict = supervise("fake", [], result, { limitMs: 50, spawner: () => child });
      t.mock.timers.tick(50);
      assert.equal((await verdict).ok, false);
      t.mock.timers.tick(20000);
      assert.deepEqual(signals, ["SIGTERM"]);
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
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "missing-smoke-"));
  try {
    const result = await supervise(
      path.join(folder, "missing-command"),
      [],
      path.join(folder, "result"),
      { limitMs: 1000 },
    );
    assert.equal(result.ok, false);
    assert.match(result.reason, /ENOENT/);
  } finally {
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

void test("verdict requires PASS and neither expired nor stopped on every platform", {}, () => {
  assert.equal(passed({ expired: false, stopped: false, verdict: "PASS" }), true);
  assert.equal(passed({ expired: true, stopped: false, verdict: "PASS" }), false);
  assert.equal(passed({ expired: false, stopped: true, verdict: "PASS" }), false);
  assert.equal(passed({ expired: false, stopped: false, verdict: "" }), false);
});

void posixTest(
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
        { timeout: 3000, encoding: "utf8", cwd: path.join(__dirname, "..") },
      );
      assert.equal(probe.status, 1, `${probe.stdout}${probe.stderr}`);
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
