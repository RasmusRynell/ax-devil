// This process remains the session/group leader until its final self-directed SIGKILL. A group number cannot be
// reused while this process is executing process.kill(-process.pid). No parent ever signals this numeric identity.
const { spawn } = require("node:child_process");
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => {});
function signalGroup(signal) {
  try {
    process.kill(-process.pid, signal);
  } catch (error) {
    if (error.code !== "ESRCH") {
      process.stderr.write(`Group cleanup failed: ${error.message}\n`);
      process.exitCode = 1;
    }
  }
}
process.on("disconnect", () => signalGroup("SIGKILL"));
// No command starts until disconnect and signal handlers are installed. The IPC channel remains referenced.
const [command, ...args] = process.argv.slice(2);
const child = spawn(command, args, { stdio: ["inherit", "inherit", "inherit"], env: process.env });
let ended = false;
function report(message) {
  if (ended) return;
  ended = true;
  if (!process.connected) return signalGroup("SIGKILL");
  process.send(message, () => signalGroup("SIGKILL"));
}
child.once("error", (error) => report({ error: error.message }));
child.once("exit", (code, signal) => report({ exited: true, code, signal }));
process.on("message", ({ signal }) => {
  if (ended) return;
  if (signal === "SIGKILL") child.kill(signal);
  else if (["SIGINT", "SIGTERM"].includes(signal)) signalGroup(signal);
});
if (process.connected) process.send({ ready: true });
else signalGroup("SIGKILL");
