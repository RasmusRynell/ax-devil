const BACKEND_INTERRUPT_MS = 3000;
const BACKEND_STOP_MS = 2 * BACKEND_INTERRUPT_MS;

/** Observes a child handle immediately; all callers share exit, timer and idempotent-stop ownership. */
function observe(child, { signal: interrupt = "SIGINT", graceMs = BACKEND_INTERRUPT_MS } = {}) {
  let ended = child.exitCode != null || child.signalCode != null;
  let stopping = false;
  const timers = new Set();
  let finish;
  const done = new Promise((resolve) => {
    finish = (result) => {
      ended = true;
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      resolve(result);
    };
  });
  child.once("exit", (code, signal) => finish({ code, signal }));
  child.once("error", (error) => finish({ error }));
  if (ended) finish({ code: child.exitCode, signal: child.signalCode });
  const signal = (name) => {
    if (ended || child.exitCode != null || child.signalCode != null) return;
    try {
      child.kill(name);
    } catch (error) {
      if (error.code !== "ESRCH") throw error;
    }
  };
  const after = (ms, name) => {
    if (!ended) timers.add(setTimeout(() => signal(name), ms));
  };
  return {
    done,
    ended: () => ended,
    signal,
    after,
    stop() {
      if (!ended && !stopping) {
        stopping = true;
        after(graceMs, "SIGKILL");
        signal(interrupt);
      }
      return done;
    },
  };
}
module.exports = { observe, BACKEND_INTERRUPT_MS, BACKEND_STOP_MS };
