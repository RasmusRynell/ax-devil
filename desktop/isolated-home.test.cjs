const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");
const { isolatedHome } = require("./smoke-home.cjs");

void test("smoke recents use the isolated Windows or macOS home even when the OS account home differs", () => {
  const windows = { HOME: "/tmp/run", USERPROFILE: "C:\\Temp\\run" };
  assert.equal(
    isolatedHome(windows, path.win32, (value) => value, "c:/Temp"),
    windows.USERPROFILE,
  );
  const mac = { HOME: "/var/folders/test/T/run" };
  const canonical = (value) => value.replace(/^\/var\//, "/private/var/");
  assert.equal(isolatedHome(mac, path.posix, canonical, "/private/var/folders/test/T"), mac.HOME);
  assert.throws(
    () => isolatedHome({ HOME: "/Users/runner" }, path.posix, canonical, "/var/folders/test/T"),
    /inside the temporary/,
  );
});
