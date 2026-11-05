const assert = require("node:assert/strict");
const fs = require("node:fs");
const { EventEmitter } = require("node:events");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");
const { after, beforeEach, test } = require("node:test");

// window.cjs takes Electron's main-process modules at load time, which a plain Node test does not have. They are
// replaced only while the module loads; the dialog stubs below are changed per test.
const root = fs.mkdtempSync(path.join(os.tmpdir(), "ax-devil-workspace-dialogs-"));
const home = path.join(root, "home");
const origin = "http://127.0.0.1:7331";
const handlers = new Map();
const calls = { opens: [], saves: [], messages: [] };
let picked;
let answer;

class FakeWebContents extends EventEmitter {
  setWindowOpenHandler() {}
  setZoomLevel() {}
  getZoomLevel() {
    return 0;
  }
  send() {}
}

class FakeBrowserWindow extends EventEmitter {
  static windows = [];

  static fromWebContents(contents) {
    return FakeBrowserWindow.windows.find((window) => window.webContents === contents) ?? null;
  }

  constructor() {
    super();
    this.webContents = new FakeWebContents();
    FakeBrowserWindow.windows.push(this);
  }

  loadURL() {
    return Promise.resolve();
  }

  isMaximized() {
    return false;
  }

  destroy() {}
}

const electron = {
  app: { getPath: () => home, getVersion: () => "0.0.0" },
  BrowserWindow: FakeBrowserWindow,
  dialog: {
    showOpenDialog: async (_window, options) => {
      calls.opens.push(options);
      return { canceled: true, filePaths: [] };
    },
    showSaveDialog: async (_window, options) => {
      calls.saves.push(options);
      return picked;
    },
    showMessageBox: async (_window, options) => {
      calls.messages.push(options);
      return { response: answer };
    },
  },
  ipcMain: { on: () => {}, handle: (channel, listener) => handlers.set(channel, listener) },
  shell: {},
};

function withFakeElectron(load) {
  const original = Module._load;
  Module._load = function (request, ...rest) {
    return request === "electron" ? electron : original.apply(this, [request, ...rest]);
  };
  try {
    return load();
  } finally {
    Module._load = original;
  }
}

const { installIpc, openWindow } = withFakeElectron(() => require("./window.cjs"));
installIpc({ connect: () => {}, disconnect: () => {}, restart: () => {} });

async function pageContents(options = {}) {
  const window = await openWindow(origin, options);
  window.webContents.mainFrame = { origin };
  return window.webContents;
}

function save(contents, name, currentPath) {
  const event = { sender: contents, senderFrame: contents.mainFrame };
  return handlers.get("workspace:pick-save")(event, name, currentPath);
}

function open(contents) {
  const event = { sender: contents, senderFrame: contents.mainFrame };
  return handlers.get("workspace:pick-open")(event);
}

beforeEach(() => {
  calls.opens = [];
  calls.saves = [];
  calls.messages = [];
  picked = { canceled: true, filePaths: [] };
  answer = 1;
});

after(() => fs.rmSync(root, { recursive: true, force: true }));

void test("the chosen name gets the workspace suffix unless it already has it; a cancelled dialog gives null", async () => {
  const page = await pageContents();
  picked = { canceled: false, filePath: path.join(root, "clip") };
  assert.equal(await save(page, "Untitled"), path.join(root, "clip.ax-devil.workspace"));
  picked = { canceled: false, filePath: path.join(root, "kept.ax-devil.workspace") };
  assert.equal(await save(page, "Untitled"), path.join(root, "kept.ax-devil.workspace"));
  picked = { canceled: true, filePaths: [] };
  assert.equal(await save(page, "Untitled"), null);
  assert.deepEqual(calls.messages, []);
  assert.equal(await open(page), null);
  assert.deepEqual(
    calls.opens.map((options) => options.filters[0].extensions),
    [["workspace"]],
  );
});

void test("the suggested name becomes one file name in the home folder, unless the workspace has a file", async () => {
  const page = await pageContents();
  await save(page, "a/b\\c\u0001d");
  await save(page, "...");
  await save(page, "Untitled", path.join(root, "current.ax-devil.workspace"));
  assert.deepEqual(
    calls.saves.map((options) => options.defaultPath),
    [
      path.join(home, "a-b-c-d.ax-devil.workspace"),
      path.join(home, "-.ax-devil.workspace"),
      path.join(root, "current.ax-devil.workspace"),
    ],
  );
  assert.ok(
    calls.saves.every((options) => options.properties.includes("showOverwriteConfirmation")),
  );
});

void test("an appended name that exists asks before it is replaced; Cancel returns null", async () => {
  const page = await pageContents();
  fs.writeFileSync(path.join(root, "taken.ax-devil.workspace"), "{}");
  picked = { canceled: false, filePath: path.join(root, "taken") };
  assert.equal(await save(page, "taken"), null);
  assert.deepEqual(
    calls.messages.map((options) => options.message),
    ["Replace taken.ax-devil.workspace?"],
  );
  answer = 0;
  assert.equal(await save(page, "taken"), path.join(root, "taken.ax-devil.workspace"));
  assert.equal(calls.messages.length, 2);
});

void test("a chosen name that already has the suffix is not asked about, and a free name is not either", async () => {
  const page = await pageContents();
  fs.writeFileSync(path.join(root, "taken.ax-devil.workspace"), "{}");
  picked = { canceled: false, filePath: path.join(root, "taken.ax-devil.workspace") };
  assert.equal(await save(page, "taken"), path.join(root, "taken.ax-devil.workspace"));
  picked = { canceled: false, filePath: path.join(root, "free") };
  assert.equal(await save(page, "free"), path.join(root, "free.ax-devil.workspace"));
  assert.deepEqual(calls.messages, []);
});

void test("the folder dialog is titled with what is chosen, and only a text title is taken", async () => {
  const page = await pageContents();
  const event = { sender: page, senderFrame: page.mainFrame };
  const pick = handlers.get("settings:pick-folder");
  assert.equal(await pick(event, "Cache folder"), null);
  assert.equal(calls.opens[0].title, "Cache folder");
  assert.deepEqual(calls.opens[0].properties, ["openDirectory"]);
  await assert.rejects(async () => pick(event, 7), /Expected a title/);
});
