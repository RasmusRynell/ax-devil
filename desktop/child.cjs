// Observe immediately after spawn; a PID belongs to us only until exit (or a failed spawn).
function observe(child, tree = false) {
  let ended = false;
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
      else child.kill(name);
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
      if (!ended) {
        signal("SIGINT");
        after(3000, "SIGKILL");
      }
      return done;
    },
  };
}
module.exports = { observe };
