const { spawnSync } = require("node:child_process");
const BACKEND_INTERRUPT_MS = 3000;
const BACKEND_STOP_MS = 2 * BACKEND_INTERRUPT_MS;
// Observe immediately after spawn; a PID belongs to us only until exit (or a failed spawn).
function observe(child, tree = false) {
  let ended = false;
  let stopping = false;
  const timers = new Set();
  const done = new Promise((resolve) => {
    const finish = (result) => {
      ended = true;
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      resolve(result);
    };
    child.once("exit", (code, signal) => finish({ code, signal }));
    child.once("error", (error) => {
      if (!child.pid) finish({ error });
      else if (error.code !== "ESRCH") throw error;
    });
  });
  const signal = (name) => {
    if (ended || child.exitCode != null || child.signalCode != null) return;
    try {
      if (tree && process.platform !== "win32") process.kill(-child.pid, name);
      else if (tree && child.pid) {
        const killed = spawnSync("taskkill", ["/F", "/T", "/PID", String(child.pid)], {
          encoding: "utf8",
        });
        if (killed.error) throw killed.error;
        if (killed.status !== 0 && !/not found/i.test(killed.stderr))
          throw new Error(`taskkill failed: ${killed.stderr}`);
      } else child.kill(name);
    } catch (error) {
      if (error.code !== "ESRCH") throw error;
    }
  };
  const after = (ms, name) => {
    if (!ended) timers.add(setTimeout(() => signal(name), ms));
  };
  return {
    done,
    signal,
    after,
    stop() {
      if (!ended && !stopping) {
        stopping = true;
        signal("SIGINT");
        after(BACKEND_INTERRUPT_MS, "SIGKILL");
      }
      return done;
    },
  };
}
module.exports = { observe, BACKEND_INTERRUPT_MS, BACKEND_STOP_MS };
