const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");

/** Observes a child immediately and owns its one interrupt/escalation sequence. */
function observe(child, { signal = "SIGINT", graceMs = 3000 } = {}) {
  let exited = child.exitCode !== null || child.signalCode !== null;
  let stopping;
  let timer;
  let resolve;
  const done = new Promise((r) => {
    resolve = r;
  });
  const finish = () => {
    exited = true;
    clearTimeout(timer);
    resolve();
  };
  child.once("exit", finish);
  child.once("error", finish);
  if (exited) finish();
  return {
    done,
    stop() {
      if (stopping) return stopping;
      stopping = done;
      if (!exited) {
        timer = setTimeout(() => child.kill("SIGKILL"), graceMs);
        child.kill(signal);
      }
      return stopping;
    },
  };
}

/**
 * Spawns through a live POSIX group leader. Only that leader signals its own group; the launcher never signals a
 * numeric group identity. IPC disconnect kills the group if the launcher dies. Windows retains direct child handles.
 */
function spawnOwned(command, args, options) {
  if (process.platform === "win32") return spawn(command, args, options);
  const leader = spawn(
    process.execPath,
    [require.resolve("./group-leader.cjs"), command, ...args],
    {
      ...options,
      detached: true,
      stdio: [
        options.stdio === "inherit" ? "inherit" : "ignore",
        options.stdio === "inherit" ? "inherit" : "pipe",
        options.stdio === "inherit" ? "inherit" : "pipe",
        "ipc",
      ],
    },
  );
  const child = new EventEmitter();
  Object.assign(child, {
    pid: leader.pid,
    stdout: leader.stdout,
    stderr: leader.stderr,
    exitCode: null,
    signalCode: null,
  });
  let status;
  let failure;
  const pending = [];
  const send = (signal) => {
    if (!leader.connected || leader.exitCode !== null || leader.signalCode !== null) return;
    leader.send({ signal }, (error) => {
      if (error && error.code !== "ERR_IPC_CHANNEL_CLOSED") {
        if (leader.connected) leader.disconnect();
        child.emit("error", error);
      }
    });
  };
  child.kill = (signal) => {
    if (status || failure) return false;
    if (child.ready) send(signal);
    else pending.push(signal);
    return true;
  };
  leader.on("message", (message) => {
    if (message.ready) {
      child.ready = true;
      for (const signal of pending) send(signal);
    } else if (message.error) {
      failure = new Error(message.error);
    } else if (message.exited) {
      status = message;
    }
  });
  leader.once("error", (error) => {
    failure = error;
    child.emit("error", error);
  });
  leader.once("exit", (code, signal) => {
    child.exitCode = status?.code ?? null;
    child.signalCode = status ? status.signal : signal;
    if (failure) child.emit("error", failure);
    else if (!status)
      child.emit(
        "error",
        new Error(`Group leader exited before reporting command status (${code ?? signal})`),
      );
    child.emit("exit", child.exitCode, child.signalCode);
  });
  leader.once("close", () => child.emit("close", child.exitCode, child.signalCode));
  return child;
}
module.exports = { observe, spawnOwned };
