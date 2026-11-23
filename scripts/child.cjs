const { spawn, spawnSync } = require("node:child_process");
const { EventEmitter } = require("node:events");

const { observe } = require("../desktop/child.cjs");

/**
 * Spawns through a live POSIX group leader. Only that leader signals its own group; the launcher never signals a
 * numeric group identity. IPC disconnect kills the group if the launcher dies. Windows retains direct child handles.
 */
function spawnOwned(command, args, { windowsTree = false, ...options }) {
  if (process.platform === "win32") {
    const child = spawn(command, args, options);
    // The fixture build already used taskkill /T on Windows; development retains its direct-child behavior.
    if (windowsTree)
      child.kill = () => {
        if (!child.pid || child.exitCode != null || child.signalCode != null) return false;
        const killed = spawnSync("taskkill", ["/F", "/T", "/PID", String(child.pid)], {
          encoding: "utf8",
        });
        if (killed.error) throw killed.error;
        if (killed.status !== 0 && !/not found/i.test(killed.stderr))
          throw new Error(`taskkill failed: ${killed.stderr}`);
        return killed.status === 0;
      };
    return child;
  }
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
    if (status || failure || leader.exitCode !== null || leader.signalCode !== null) return false;
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
        new Error(
          `Group leader exited before reporting command status (${code ?? signal}); cleanup was not confirmed and descendants may remain`,
        ),
      );
    child.emit("exit", child.exitCode, child.signalCode);
  });
  leader.once("close", () => child.emit("close", child.exitCode, child.signalCode));
  return child;
}
module.exports = { observe, spawnOwned };
