const { spawn } = require("node:child_process");
const fs = require("node:fs");
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
    signalGroup = (pid, name) => process.kill(pid, name),
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
    const pass = (chunk) => {
      process.stdout.write(chunk);
      for (const match of chunk.toString().matchAll(/smoke: running (\S+)/g)) last = match[1];
    };
    child.stdout.on("data", pass);
    child.stderr.on("data", pass);
    // The launch owns its process group: the id is signalled only while `pid` is set, and `finish` retires it.
    const owned = {
      pid: child.pid,
      stopped: false,
      killedGroup: false,
      done: false,
      escalation: undefined,
      deadline: undefined,
      sweep: undefined,
      verdictPoll: undefined,
      shutdown: undefined,
    };
    const signal = (name) => {
      if (owned.pid === undefined || (name === "SIGKILL" && owned.killedGroup)) return;
      if (name === "SIGKILL") owned.killedGroup = true;
      try {
        signalGroup(-owned.pid, name);
      } catch (error) {
        if (error.code !== "ESRCH") {
          owned.stopped = true;
          process.stderr.write(`group "${group}": ${name} failed: ${error.message}\n`);
        }
      }
    };
    const stop = () => {
      if (owned.stopped) return;
      owned.stopped = true;
      // After the app has exited there is no group left to signal, but the launch is still cancelled.
      if (owned.pid === undefined) return;
      owned.escalation = setTimeout(() => signal("SIGKILL"), killAfterMs);
      signal("SIGTERM");
      owned.escalation.unref();
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
    // The app has exited, so its group id is certain only now: end what is left of the group at once, and let no later
    // path signal it (the id could be reused). A descendant that still holds the launcher's output pipes, or one in
    // another session, delays `close`; after the grace the pipes are cut, so the launch finishes through `finish`.
    // This is cleanup, not a stop: it changes no verdict.
    child.on("exit", () => {
      clearTimeout(owned.deadline);
      clearInterval(owned.verdictPoll);
      clearTimeout(owned.shutdown);
      clearTimeout(owned.escalation);
      signal("SIGKILL");
      owned.pid = undefined;
      owned.sweep = setTimeout(() => {
        owned.stopped = true;
        killed =
          "output still held after exit: SIGKILL reached only the original process group; a process in another session may still be running";
        process.stderr.write(
          `group "${group}": the app's output was still held open after it exited; the group was killed and the output closed (a process outside the app's process group may still be running)\n`,
        );
        child.stdout.destroy();
        child.stderr.destroy();
        finish(child.exitCode);
      }, exitGraceMs);
    });
    // Runs once per launch, from `error` (spawn failed) or `close`, whichever comes first; the other does nothing.
    const finish = (code, spawnError) => {
      if (owned.done) return;
      owned.done = true;
      clearTimeout(owned.deadline);
      clearInterval(owned.verdictPoll);
      clearTimeout(owned.shutdown);
      clearTimeout(owned.escalation);
      clearTimeout(owned.sweep);
      owned.pid = undefined;
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
      const ok = !spawnError && !owned.stopped && code === 0 && verdict === "PASS";
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
