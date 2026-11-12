const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createDeviceValues } = require("./device.cjs");

void test("a device save retains malformed neighboring preferences and existing file permissions", async (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  const original = { "ui.theme": { name: "dark" }, "ui.text_size": "wrong" };
  fs.writeFileSync(file, JSON.stringify(original), { mode: 0o640 });
  const device = createDeviceValues(file);
  await device.write({ ["ui.text_size"]: "large" });
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), {
    ...original,
    "ui.text_size": "large",
  });
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o640);
  await device.write({ ["ui.theme"]: "dark" });
  assert.equal(createDeviceValues(file).read().values["ui.theme"], "dark");
});

void test("device writes use private exclusive files and refuse non-regular targets", async (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-safe-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  const linked = path.join(folder, "unrelated.json");
  fs.writeFileSync(linked, "untouched", { mode: 0o600 });
  if (process.platform !== "win32") fs.symlinkSync(linked, `${file}.partial`);
  const device = createDeviceValues(file);
  await device.write({ ["ui.theme"]: "dark" });
  assert.equal(fs.readFileSync(linked, "utf8"), "untouched");
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(linked).mode & 0o777, 0o600);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    await device.write({ ["ui.text_size"]: "large" });
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    fs.unlinkSync(file);
    fs.symlinkSync(linked, file);
    await assert.rejects(device.write({ ["ui.theme"]: "light" }), /regular file/);
    assert.equal(fs.lstatSync(file).isSymbolicLink(), true);
    assert.equal(fs.readFileSync(linked, "utf8"), "untouched");
    assert.equal(fs.statSync(linked).mode & 0o777, 0o600);
    fs.unlinkSync(file);
  } else fs.unlinkSync(file);
  fs.mkdirSync(file);
  await assert.rejects(device.write({ ["ui.theme"]: "light" }), /regular file/);
  assert.equal(fs.lstatSync(file).isDirectory(), true);
  if (process.platform !== "win32") {
    fs.rmdirSync(file);
    execFileSync("mkfifo", [file]);
    // Reading a FIFO would block too; the store must refuse it before attempting a read.
    await assert.rejects(createDeviceValues(file).write({ ["ui.theme"]: "light" }), /regular file/);
    assert.equal(fs.lstatSync(file).isFIFO(), true);
  }
});

void test("a failed device replacement reports its reason, removes its temporary file and keeps the prior state", async (t) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-failure-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const file = path.join(folder, "device.json");
  fs.writeFileSync(file, JSON.stringify({ "ui.theme": "dark" }), { mode: 0o600 });
  const device = createDeviceValues(file);
  const announcements = [];
  device.subscribe((snapshot) => announcements.push(snapshot));
  t.mock.method(fs.promises, "rename", () => {
    throw new Error("replacement refused");
  });
  await assert.rejects(device.write({ ["ui.theme"]: "light" }), /replacement refused/);
  assert.deepEqual(fs.readdirSync(folder), ["device.json"]);
  assert.deepEqual(device.read(), { values: { "ui.theme": "dark" }, sequence: 0 });
  assert.deepEqual(announcements, []);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), device.read().values);
});

void test(
  "overlapping Device batches persist in arrival order, and quit drains the owner",
  { timeout: 5000 },
  async (t) => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-device-order-"));
    t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
    const file = path.join(folder, "device.json");
    fs.writeFileSync(file, JSON.stringify({ "ui.theme": "auto" }), { mode: 0o600 });
    const device = createDeviceValues(file);
    let release;
    const blocked = new Promise((resolve) => {
      release = resolve;
    });
    let entered;
    const started = new Promise((resolve) => {
      entered = resolve;
    });
    const rename = fs.promises.rename;
    const writes = [];
    t.mock.method(fs.promises, "rename", async (source, target) => {
      writes.push(JSON.parse(await fs.promises.readFile(source, "utf8")));
      if (writes.length === 1) {
        entered();
        await blocked;
      }
      return rename(source, target);
    });
    const announcements = [];
    device.subscribe((snapshot) => announcements.push(snapshot));
    const first = device.write({ "ui.theme": "dark" }, "first-batch");
    const second = device.write({ "ui.text_size": "large" }, "second-batch");
    assert.deepEqual(device.read().values, { "ui.theme": "auto" });
    await started;
    assert.equal(writes.length, 1);
    const drained = device.close();
    await assert.rejects(device.write({ "ui.theme": "light" }), /closing/);
    release();
    await Promise.all([first, second, drained]);
    assert.deepEqual(
      announcements.map(({ batch }) => batch),
      ["first-batch", "second-batch"],
    );
    assert.deepEqual(writes, [
      { "ui.theme": "dark" },
      { "ui.theme": "dark", "ui.text_size": "large" },
    ]);
    assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), device.read().values);
  },
);
