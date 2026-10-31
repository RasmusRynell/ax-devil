const assert = require("node:assert/strict");
const { test } = require("node:test");
const { backendArgs, userArgs } = require("./args.cjs");

void test("the command and its options go to the backend, the shell's and Chromium's switches do not", () => {
  const argv = ["electron", ".", "--ozone-platform=x11", "--smoke-test", "--log-level", "DEBUG"];
  assert.deepEqual(backendArgs(userArgs(argv, true)), ["--log-level", "DEBUG"]);
  assert.deepEqual(
    backendArgs(["--config=a.json", "--no-sandbox", "local", "--video", "a b.mp4"]),
    ["--config=a.json", "local", "--video", "a b.mp4"],
  );
  assert.deepEqual(userArgs(["ax-devil", "local", "--video", "x.mp4"], false), [
    "local",
    "--video",
    "x.mp4",
  ]);
});
