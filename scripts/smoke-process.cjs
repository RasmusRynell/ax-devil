const { spawnOwned: spawn, observe } = require("./child.cjs");
const fs = require("node:fs");
const { passed } = require("./smoke-verdict.cjs");
const KILL_AFTER_MS = 10_000;
const EXIT_GRACE_MS = 5000;

/**
 * Starts one launch and resolves with its verdict. It passes only when the app exited 0 and wrote PASS to `result`,
 * the file `AX_DEVIL_SMOKE_RESULT` names; an app that was stopped before it judged exits 0 with no verdict.
 */
function supervise(
  command,
  args,
  result,
  {
    group = "packaged",
    names = [],
    limitMs,
    spawner = spawn,
    exitGraceMs = EXIT_GRACE_MS,
    killAfterMs = KILL_AFTER_MS,
  } = {},
) {
  const limit = limitMs;
  const began = Date.now();
  process.stdout.write(`smoke: group ${group}: ${names.join(", ")}\n`);
  return new Promise((resolve) => {
    const child = spawner(command, args, {
      detached: process.platform !== "win32",
      env: { ...process.env, AX_DEVIL_SMOKE_RUN: names.join(","), AX_DEVIL_SMOKE_RESULT: result },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let last = "start";
    let killed = "";
    let finished = false;
    const pass = (chunk) => {
      process.stdout.write(chunk);
      for (const match of chunk.toString().matchAll(/smoke: running (\S+)/g)) last = match[1];
    };
    child.stdout.on("data", pass);
    child.stderr.on("data", pass);
    const owned = observe(child, { signal: "SIGTERM", graceMs: killAfterMs });
    owned.stopped = false;
    const stop = () => {
      if (owned.stopped) return;
      owned.stopped = true;
      void owned.stop();
    };
    const cancel = () => {
      killed = "cancelled";
      stop();
    };
    process.on("SIGINT", cancel);
    process.on("SIGTERM", cancel);
    owned.verdictPoll = setInterval(() => {
      if (!fs.existsSync(result)) return;
      clearInterval(owned.verdictPoll);
      owned.shutdown = setTimeout(() => {
        killed = "app required forced stopping after its verdict";
        stop();
      }, killAfterMs);
    }, 100);
    owned.deadline = setTimeout(() => {
      killed = `killed after ${Math.round((Date.now() - began) / 1000)} s with no verdict, while running "${last}"`;
      process.stderr.write(`group "${group}" ${killed}\n`);
      stop();
    }, limit);
    // The leader has completed group cleanup before this exit. Never signal its now-retired identity.
    child.on("exit", () => {
      if (finished) return;
      clearTimeout(owned.deadline);
      clearInterval(owned.verdictPoll);
      clearTimeout(owned.shutdown);
      owned.sweep = setTimeout(() => {
        owned.stopped = true;
        killed = `group "${group}": escaped-session survivor still holds output; only the original group was cleaned; escaped sessions were not targeted`;
        process.stderr.write(`${killed}\n`);
        child.stdout.destroy();
        child.stderr.destroy();
        finish(child.exitCode);
      }, exitGraceMs);
    });
    // Runs once per launch, from `error` (spawn failed) or `close`, whichever comes first; the other does nothing.
    const finish = (code, spawnError) => {
      if (finished) return;
      finished = true;
      if (spawnError) {
        child.stdout?.destroy();
        child.stderr?.destroy();
      }
      clearTimeout(owned.deadline);
      clearInterval(owned.verdictPoll);
      clearTimeout(owned.shutdown);
      clearTimeout(owned.sweep);
      process.removeListener("SIGINT", cancel);
      process.removeListener("SIGTERM", cancel);
      const seconds = Math.round((Date.now() - began) / 1000);
      let verdict = "";
      try {
        verdict = fs.readFileSync(result, "utf8").trim();
      } catch {
        // The app never judged.
      }
      // A launch that was told to stop (deadline or cancel) never passes, whatever it wrote or exited with.
      const ok =
        !spawnError && code === 0 && passed({ expired: false, stopped: owned.stopped, verdict });
      const reason = ok
        ? ""
        : spawnError?.message ||
          killed ||
          (owned.stopped ? `cancelled while running "${last}"` : "") ||
          (verdict && verdict !== "PASS" ? verdict : `no verdict (exit ${code}), in "${last}"`);
      resolve({ ok, seconds, reason });
    };
    child.on("error", (error) => finish(null, error));
    child.on("close", (code) => finish(code));
  });
}

module.exports = { supervise, KILL_AFTER_MS };
if (require.main === module) {
  const [limit, result, names, command, ...args] = process.argv.slice(2);
  void supervise(command, args, result, {
    limitMs: Number(limit) * 1000,
    names: names.split(","),
  }).then(({ ok, reason }) => {
    if (!ok) process.stderr.write(`FAIL: ${reason}\n`);
    process.exitCode = ok ? 0 : 1;
  });
}
