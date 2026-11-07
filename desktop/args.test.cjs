const assert = require("node:assert/strict");
const { test } = require("node:test");
const { backendArgs, parseLaunch, relaunchArgs, userArgs } = require("./args.cjs");

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

void test("a server address or link opens a window and is not taken for the backend's command", () => {
  const argv = userArgs(
    ["electron", ".", "--server", "http://nas:7340", "--log-level=DEBUG"],
    true,
  );
  const { launch, rest } = parseLaunch(argv);
  assert.deepEqual(launch, { server: "http://nas:7340" });
  assert.deepEqual(backendArgs(rest), ["--log-level=DEBUG"]);

  const linked = parseLaunch([
    "ax-devil://open?url=http%3A%2F%2Fnas%3A7340",
    "local",
    "--video",
    "a.mp4",
  ]);
  assert.deepEqual(linked.launch, { link: "ax-devil://open?url=http%3A%2F%2Fnas%3A7340" });
  assert.deepEqual(backendArgs(linked.rest), ["local", "--video", "a.mp4"]);
  assert.deepEqual(parseLaunch(["--server=http://nas"]).launch, { server: "http://nas" });
  assert.throws(() => parseLaunch(["--server"]), /needs a server address/);
  // A link the system delivered is not redirected by an argument that came along with it.
  assert.deepEqual(parseLaunch(["ax-devil://open", "--server=http://evil"]).launch, {
    link: "ax-devil://open",
  });
});

void test("a restart keeps the global options and switches but not the server, link or intake command", () => {
  const argv = [
    "electron",
    ".",
    "--ozone-platform=x11",
    "--server",
    "http://nas:7340",
    "--config=a.json",
    "local",
    "--video",
    "recording.mp4",
    "--cache-dir",
    "/c",
  ];
  assert.deepEqual(relaunchArgs(argv, true), [
    ".",
    "--ozone-platform=x11",
    "--config=a.json",
    "--cache-dir",
    "/c",
  ]);
  assert.deepEqual(
    relaunchArgs(
      ["ax-devil", "ax-devil://open?url=x", "--log-level=DEBUG", "open", "a.ax-devil.workspace"],
      false,
    ),
    ["--log-level=DEBUG"],
  );
});
