const { spawnOwned: spawn, observe } = require("./child.cjs");
const { FIXTURE_BUILD_MS, EXIT_GRACE_MS } = require("../desktop/smoke-budget.cjs");

// A live private group leader owns the build through cleanup. An escaped descendant can hold the pipes;
// report that as failure, close our ends, and never signal the retired group id.
async function build(
  command,
  args,
  {
    spawner = spawn,
    limit = FIXTURE_BUILD_MS,
    grace = EXIT_GRACE_MS,
    output = (data) => process.stdout.write(data),
    report = (message) => process.stderr.write(`${message}\n`),
  } = {},
) {
  const child = spawner(command, args, {
    detached: true,
    windowsTree: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const owned = observe(child);
  const closed = new Promise((resolve) => child.once("close", resolve));
  child.stdout.on("data", output);
  child.stderr.on("data", output);
  let expired = false;
  const timer = setTimeout(() => {
    expired = true;
    report("Loopback fixture build exceeded its budget; stopping its process tree");
    owned.signal("SIGKILL");
  }, limit);
  const { code, error } = await owned.done;
  clearTimeout(timer);
  let graceTimer;
  const drained = await Promise.race([
    closed.then(() => true),
    new Promise((resolve) => {
      graceTimer = setTimeout(() => resolve(false), grace);
    }),
  ]);
  clearTimeout(graceTimer);
  if (!drained) {
    report(
      "Build output remained open after its owner exited; closing pipes. A descendant may still be running; no retired PID was signalled.",
    );
    child.stdout.destroy();
    child.stderr.destroy();
  }
  if (error) report(error.message);
  return !expired && drained && !error && code === 0 ? 0 : 1;
}
if (require.main === module)
  void build(process.argv[2], process.argv.slice(3)).then((code) => {
    process.exitCode = code;
  });
module.exports = { build };
