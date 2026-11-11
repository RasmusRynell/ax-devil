// Observe at spawn, before callers can start waiting or ask for shutdown.
const STOP_GRACE_MS = 3000;

/** Owns a child's exit and its one interrupt/escalation sequence. Groups are private POSIX launch groups. */
function observe(child, group = false) {
  let exited = child.exitCode !== null || child.signalCode !== null;
  let stopping;
  let killed = false;
  let timer;
  let resolve;
  const done = new Promise((r) => {
    resolve = r;
  });
  const signal = (name) => {
    if (exited || !child.pid || (name === "SIGKILL" && killed)) return;
    if (name === "SIGKILL") killed = true;
    try {
      if (group && process.platform !== "win32") process.kill(-child.pid, name);
      else child.kill(name);
    } catch (error) {
      if (error.code !== "ESRCH") throw error;
    }
  };
  const finish = () => {
    if (exited) return;
    clearTimeout(timer);
    // Sweep synchronously at exit, never from a later timer with a retired group id.
    if (group && process.platform !== "win32") signal("SIGKILL");
    exited = true;
    resolve();
  };
  child.once("exit", finish);
  child.once("error", finish);
  if (exited) resolve();
  return {
    done,
    stop() {
      if (stopping) return stopping;
      stopping = done;
      if (!exited) {
        timer = setTimeout(() => signal("SIGKILL"), STOP_GRACE_MS);
        signal("SIGINT");
      }
      return stopping;
    },
  };
}
module.exports = { observe };
