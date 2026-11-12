const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createDeviceValues } = require("./device.cjs");

void test("a device save retains malformed neighboring preferences and existing file permissions", (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  const original = { "ui.theme": { name: "dark" }, "ui.text_size": "wrong" };
  fs.writeFileSync(file, JSON.stringify(original), { mode: 0o640 });
  const device = createDeviceValues(file);
  device.write({ ["ui.text_size"]: "large" });
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), {
    ...original,
    "ui.text_size": "large",
  });
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o640);
  device.write({ ["ui.theme"]: "dark" });
  assert.equal(createDeviceValues(file).read()["ui.theme"], "dark");
});

void test("device writes use private exclusive files and refuse non-regular targets", (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-safe-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  const linked = path.join(folder, "unrelated.json");
  fs.writeFileSync(linked, "untouched", { mode: 0o600 });
  if (process.platform !== "win32") fs.symlinkSync(linked, `${file}.partial`);
  const device = createDeviceValues(file);
  assert.equal(device.write({ ["ui.theme"]: "dark" }), null);
  assert.equal(fs.readFileSync(linked, "utf8"), "untouched");
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(linked).mode & 0o777, 0o600);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.equal(device.write({ ["ui.text_size"]: "large" }), null);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    fs.unlinkSync(file);
    fs.symlinkSync(linked, file);
    assert.match(device.write({ ["ui.theme"]: "light" }), /regular file/);
    assert.equal(fs.lstatSync(file).isSymbolicLink(), true);
    assert.equal(fs.readFileSync(linked, "utf8"), "untouched");
    assert.equal(fs.statSync(linked).mode & 0o777, 0o600);
    fs.unlinkSync(file);
  } else fs.unlinkSync(file);
  fs.mkdirSync(file);
  assert.match(device.write({ ["ui.theme"]: "light" }), /regular file/);
  assert.equal(fs.lstatSync(file).isDirectory(), true);
  if (process.platform !== "win32") {
    fs.rmdirSync(file);
    execFileSync("mkfifo", [file]);
    // Reading a FIFO would block too; the store must refuse it before attempting a read.
    assert.match(createDeviceValues(file).write({ ["ui.theme"]: "light" }), /regular file/);
    assert.equal(fs.lstatSync(file).isFIFO(), true);
  }
});

void test("a failed device replacement reports its reason, removes its temporary file and keeps the prior state", (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-failure-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  fs.writeFileSync(file, JSON.stringify({ "ui.theme": "dark" }), { mode: 0o600 });
  const device = createDeviceValues(file);
  t.mock.method(fs, "renameSync", () => {
    throw new Error("replacement refused");
  });
  assert.match(device.write({ ["ui.theme"]: "light" }), /replacement refused/);
  assert.deepEqual(fs.readdirSync(folder), ["device.json"]);
  assert.deepEqual(device.read(), { "ui.theme": "dark" });
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), device.read());
});
