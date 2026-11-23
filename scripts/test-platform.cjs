const assert = require("node:assert/strict");
const { after, test: nodeTest } = require("node:test");
const posixSkip =
  process.platform === "win32" ? "Requires POSIX process groups, signals or shell fixtures" : false;

/** Registers portable/POSIX tests and rejects an entirely skipped file on Linux CI. */
function platformTests() {
  let executed = 0;
  after(() => {
    if (process.platform === "linux")
      assert.ok(executed > 0, "Linux must execute tests in this file");
  });
  const test = (name, options, run) =>
    nodeTest(name, options, async (context) => {
      executed += 1;
      await run(context);
    });
  return {
    test,
    posixTest: (name, options, run) => test(name, { ...options, skip: posixSkip }, run),
  };
}
module.exports = { platformTests };
