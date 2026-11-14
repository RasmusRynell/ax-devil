const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const { linkTarget, registerLinks, singleInstance } = require("./links.cjs");

const LINK = `ax-devil://open?url=${encodeURIComponent("http://nas:7340/#join=abc")}`;

function fakeApp({ lock = true, packaged = true, registers = true } = {}) {
  const app = new EventEmitter();
  app.isPackaged = packaged;
  app.requestSingleInstanceLock = () => lock;
  app.registered = [];
  app.setAsDefaultProtocolClient = (scheme) => {
    app.registered.push(scheme);
    return registers;
  };
  return app;
}

void test("a link holds only an http(s) address, and only as an ax-devil link", () => {
  assert.equal(linkTarget(LINK), "http://nas:7340/#join=abc");
  for (const bad of [
    "ax-devil://open",
    "ax-devil://open?url=file%3A%2F%2F%2Fetc%2Fpasswd",
    "ax-devil://other?url=http%3A%2F%2Fnas",
    `https://open/?url=${encodeURIComponent("http://nas")}`,
  ])
    assert.throws(() => linkTarget(bad), /does not hold a web address/, bad);
});

void test("a later launch reaches the running instance, and one that cannot be read is reported", () => {
  const launches = [];
  const errors = [];
  const app = fakeApp();
  const dialog = { showErrorBox: (title, message) => errors.push([title, message]) };
  assert.equal(
    singleInstance((launch) => launches.push(launch), { app, dialog }),
    true,
  );

  // The later launch's own arguments, and the folder it was typed in, which relative paths in a command mean.
  const later = (args, cwd = "/") => app.emit("second-instance", {}, [], cwd, { args });
  later([LINK]);
  later(["--server=http://nas:7340"]);
  later([]);
  // A global option alone is no command, and a server wins over one.
  later(["--log-level=DEBUG", "local", "--video", "a.mp4"], "/rec");
  later(["--log-level=DEBUG"], "/rec");
  later(["--server=http://nas:7340", "local", "--video", "a.mp4"], "/rec");
  assert.deepEqual(launches, [
    { link: LINK },
    { server: "http://nas:7340" },
    {},
    { command: ["--log-level=DEBUG", "local", "--video", "a.mp4"], cwd: "/rec" },
    {},
    { server: "http://nas:7340" },
  ]);

  later(["--server"]);
  assert.equal(launches.length, 6);
  assert.equal(errors.length, 1);

  let prevented = 0;
  const event = { preventDefault: () => prevented++ };
  app.emit("open-url", event, LINK);
  app.emit("open-url", event, "https://example.com/");
  assert.deepEqual(launches.slice(6), [{ link: LINK }]);
  assert.equal(prevented, 2);
});

void test("a second instance quits instead of running", () => {
  const app = fakeApp({ lock: false });
  assert.equal(
    singleInstance(() => assert.fail("not delivered here"), { app }),
    false,
  );
  assert.equal(app.listenerCount("second-instance"), 0);
});

void test("only a packaged app takes the ax-devil link, and a refusal is reported", () => {
  const reports = [];
  const report = (error) => reports.push(error.message);

  const development = fakeApp({ packaged: false });
  registerLinks(report, { app: development });
  assert.deepEqual(development.registered, []);

  const packaged = fakeApp();
  registerLinks(report, { app: packaged });
  assert.deepEqual(packaged.registered, ["ax-devil"]);
  assert.deepEqual(reports, []);

  registerLinks(report, { app: fakeApp({ registers: false }) });
  assert.equal(reports.length, 1);
});
