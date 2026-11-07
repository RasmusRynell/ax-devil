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
let opened;
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
      return opened;
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

function exportTo(contents, name, extension) {
  const event = { sender: contents, senderFrame: contents.mainFrame };
  return handlers.get("export:pick-file")(event, name, extension);
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
  opened = { canceled: true, filePaths: [] };
  answer = 1;
});

after(() => fs.rmSync(root, { recursive: true, force: true }));

void test("the chosen name gets the workspace suffix unless it already has it; a cancelled dialog gives null", async () => {
  const page = await pageContents();
  picked = { canceled: false, filePath: path.join(root, "clip") };
  assert.equal(await save(page, "Untitled"), path.join(root, "clip.ax-devil.workspace"));
  assert.deepEqual(calls.saves.at(-1).filters, [
    { name: "Workspaces (*.ax-devil.workspace)", extensions: ["ax-devil.workspace"] },
  ]);
  picked = { canceled: false, filePath: path.join(root, "kept.ax-devil.workspace") };
  assert.equal(await save(page, "Untitled"), path.join(root, "kept.ax-devil.workspace"));
  picked = { canceled: true, filePaths: [] };
  assert.equal(await save(page, "Untitled"), null);
  assert.deepEqual(calls.messages, []);
  picked = { canceled: false, filePath: path.join(root, "clip") };
  assert.equal(await exportTo(page, "v.mp4", "mp4"), path.join(root, "clip.mp4"));
  picked = { canceled: false, filePath: path.join(root, "kept.PNG") };
  assert.equal(await exportTo(page, "v.png", "png"), path.join(root, "kept.PNG"));
  assert.deepEqual(calls.saves.at(-1).filters, [{ name: "PNG Image", extensions: ["png"] }]);
  picked = { canceled: true, filePaths: [] };
  assert.equal(await exportTo(page, "v.mp4", "mp4"), null);
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
  await exportTo(page, "v.mp4", "mp4");
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
  fs.writeFileSync(path.join(root, "taken.mp4"), "");
  picked = { canceled: false, filePath: path.join(root, "taken") };
  answer = 1;
  assert.equal(await exportTo(page, "taken.mp4", "mp4"), null);
  assert.equal(calls.messages.at(-1).message, "Replace taken.mp4?");
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

void test("the file dialog takes one video or overlay file, filtered as asked, and gives null when cancelled", async () => {
  const page = await pageContents();
  const event = { sender: page, senderFrame: page.mainFrame };
  const pick = handlers.get("workspace:pick-file");
  assert.equal(await pick(event, "video"), null);
  assert.equal(await pick(event, "overlay", [".json", ".tar.gz"]), null);
  assert.equal(await pick(event, "overlay", []), null);
  assert.deepEqual(
    calls.opens.map(({ title, properties, filters }) => ({ title, properties, filters })),
    [
      {
        title: "Select Video File",
        properties: ["openFile"],
        filters: [
          {
            name: "Video Files (*.mp4 *.avi *.mkv *.mov)",
            extensions: ["mp4", "avi", "mkv", "mov"],
          },
          { name: "All Files (*)", extensions: ["*"] },
        ],
      },
      {
        title: "Select Overlay File",
        properties: ["openFile"],
        filters: [
          { name: "Data Files (*.json *.tar.gz)", extensions: ["json", "tar.gz"] },
          { name: "All Files (*)", extensions: ["*"] },
        ],
      },
      {
        title: "Select Overlay File",
        properties: ["openFile"],
        filters: [{ name: "All Files (*)", extensions: ["*"] }],
      },
    ],
  );
  opened = { canceled: false, filePaths: ["/data/a.json"] };
  assert.equal(await pick(event, "overlay", [".json"]), "/data/a.json");
  await assert.rejects(async () => pick(event, "audio"), /video or overlay/);
  await assert.rejects(async () => pick(event, "overlay", ["json"]), /extensions/);
  await assert.rejects(async () => pick(event, "overlay"), /extensions/);
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
