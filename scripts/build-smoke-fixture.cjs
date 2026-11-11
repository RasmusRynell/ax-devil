const { spawn } = require("node:child_process");
const { observe } = require("../desktop/child.cjs");
const { FIXTURE_BUILD_MS } = require("../desktop/smoke-budget.cjs");
const child = spawn(process.argv[2], process.argv.slice(3), { stdio: "inherit" });
const owned = observe(child);
const timer = setTimeout(() => {
  process.stderr.write("Loopback fixture build exceeded its budget\n");
  owned.signal("SIGKILL");
}, FIXTURE_BUILD_MS);
void owned.done.then(({ code, error }) => {
  clearTimeout(timer);
  if (error) process.stderr.write(`${error.message}\n`);
  process.exitCode = code ?? 1;
});
