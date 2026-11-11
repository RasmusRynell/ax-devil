// Windows and the IPC they may use. A window trusts exactly one origin, fixed when it is created: the page there
// may use the shell's IPC, navigation to any other origin is blocked, and new windows are denied. The one link a page
// may open is the project's, in the default browser.
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const { reserved } = require("./generated/api.json");
const { restoreDrag } = require("./restore-drag.cjs");
const { windowBounds } = require("./window-state.cjs");

// Video files the Add Video dialog offers; the backend's intake takes any path, the viewer judges the media.
const VIDEO_EXTENSIONS = [".mp4", ".avi", ".mkv", ".mov"];

// The most extensions, and the longest one, an overlay dialog request may carry.
const MAX_EXTENSIONS = 1000;
const MAX_EXTENSION_LENGTH = 256;

// The extensions a native dialog filter can state: dotted suffixes of plain characters. The backend decides what a
// decoder may declare; any other suffix is left out of the filter, which All Files always backs up.
const FILTERABLE_EXTENSION = /^(\.[a-z0-9_+-]+)+$/;

/** The open dialog's filters: the `extensions` (with their dots) a filter can state under `label`, then every file. */
function fileFilters(label, extensions) {
  const stated = extensions.filter((one) => FILTERABLE_EXTENSION.test(one));
  const name = `${label} Files (${stated.map((one) => `*${one}`).join(" ")})`;
  return [
    ...(stated.length ? [{ name, extensions: stated.map((one) => one.slice(1)) }] : []),
    { name: "All Files (*)", extensions: ["*"] },
  ];
}

// What an export writes: the file dialog's filter name by file extension; the page names one of these.
const EXPORT_FILTERS = { mp4: "MP4 Video", png: "PNG Image", csv: "CSV" };

// The extension of a saved workspace file.
const WORKSPACE_SUFFIX = ".ax-devil.workspace";

// Zoom keys the shell handles itself (the backend refuses them as shortcuts), by the key that triggers them.
const ZOOM_STEPS = { "=": 0.5, "+": 0.5, "-": -0.5, 0: null };

// The name of the file the backend logs to, in a folder of its own.
const LOG_FILE_NAME = "ax-devil.log";

// The project link the About dialog offers (the page and this constant must agree).
const PROJECT_LINK = "https://github.com/rasmusrynell/ax-devil";

// Tells the preload that the page may offer to restart the app.
const RESTARTABLE_ARGUMENT = "--ax-devil-restartable";
// Tells the preload that the page is served by another machine's backend.
const REMOTE_ARGUMENT = "--ax-devil-remote";

// Window contents -> { origin, remote }; `remote` is a page served by another machine's backend.
const trusted = new WeakMap();

/**
 * Opens a window on `origin` and resolves once its page has loaded; `url` (default: the origin) is what it loads.
 * `bounds` is its position and size (default: the standard size). `restartable` is whether the page may offer to
 * restart the app, which only helps when the app started the backend the page shows. A failed load destroys the window
 * and rejects.
 */
async function openWindow(
  origin,
  {
    url = origin,
    remote = false,
    restartable = false,
    fatal,
    bounds = windowBounds(undefined, []),
  },
) {
  const window = new BrowserWindow({
    title: "ax-devil",
    ...bounds,
    minWidth: 640,
    minHeight: 480,
    // On macOS a window cannot be larger than its screen, and a CI runner's screen is small; the smoke test's sizes
    // do not depend on the screen.
    enableLargerThanScreen: process.argv.includes("--smoke-test"),
    // The page draws its own title bar and window controls (see preload.cjs).
    frame: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      // Smoke test windows overlap, and an overlapped page must keep drawing (see main.cjs).
      backgroundThrottling: !process.argv.includes("--smoke-test"),
      preload: path.join(__dirname, "preload.cjs"),
      additionalArguments: [
        ...(restartable ? [RESTARTABLE_ARGUMENT] : []),
        ...(remote ? [REMOTE_ARGUMENT] : []),
      ],
    },
  });
  const contents = window.webContents;
  trusted.set(contents, { origin, remote });
  const sendMaximized = () => contents.send("window:maximized", window.isMaximized());
  window.on("maximize", sendMaximized);
  window.on("unmaximize", sendMaximized);
  const sendFullScreen = () => contents.send("window:fullscreen", window.isFullScreen());
  window.on("enter-full-screen", sendFullScreen);
  window.on("leave-full-screen", sendFullScreen);
  // There is no application menu, so zoom is handled here; Chromium remembers it per host.
  contents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown" || !input.control || input.alt) return;
    const step = ZOOM_STEPS[input.key];
    if (step === undefined || !reserved.includes(`Ctrl+${input.key}`)) return;
    contents.setZoomLevel(step === null ? 0 : contents.getZoomLevel() + step);
    event.preventDefault();
  });
  contents.setWindowOpenHandler(({ url: target }) => {
    if (target === PROJECT_LINK) void shell.openExternal(target);
    return { action: "deny" };
  });
  const stayOnOrigin = (event, target) => {
    if (new URL(target).origin !== origin) event.preventDefault();
  };
  contents.on("will-navigate", stayOnOrigin);
  contents.on("will-redirect", stayOnOrigin);
  contents.on("render-process-gone", (_event, details) =>
    fatal(new Error(`Renderer stopped: ${details.reason}`)),
  );
  window.on("unresponsive", () => fatal(new Error("Desktop window stopped responding")));
  try {
    await window.loadURL(url);
  } catch (error) {
    window.destroy();
    throw error;
  }
  return window;
}

/** The open windows that show the local backend; the others show a remote server. */
function localWindows() {
  return BrowserWindow.getAllWindows().filter(
    (window) => trusted.get(window.webContents)?.remote === false,
  );
}

// The window's entry when the message comes from the main frame of the page at the origin it trusts.
function sender(event) {
  const entry = trusted.get(event.sender);
  const frame = event.senderFrame;
  if (!entry || !frame || frame !== event.sender.mainFrame || frame.origin !== entry.origin)
    throw new Error("Not allowed");
  return { ...entry, window: BrowserWindow.fromWebContents(event.sender) };
}

/**
 * The path a save dialog's `chosen` result names, null when it was cancelled. `suffix` is appended unless the name
 * has it (any case); only an appended name that already exists asks before it is replaced, since the dialog
 * could not know it. Cancel returns null.
 */
async function suffixed(window, chosen, suffix) {
  if (chosen.canceled) return null;
  if (chosen.filePath.toLowerCase().endsWith(suffix.toLowerCase())) return chosen.filePath;
  const target = `${chosen.filePath}${suffix}`;
  if (fs.existsSync(target)) {
    const answer = await dialog.showMessageBox(window, {
      type: "warning",
      message: `Replace ${path.basename(target)}?`,
      buttons: ["Replace", "Cancel"],
      defaultId: 1,
      cancelId: 1,
    });
    if (answer.response !== 0) return null;
  }
  return target;
}

/**
 * Registers every IPC handler, each answering only its own window's trusted page. `connect` is honoured from a
 * local window and `disconnect` from a remote one, and `restart` from a local one, by the callbacks given here.
 */
function installIpc({ connect, disconnect, restart, device }) {
  const on = (channel, act) =>
    ipcMain.on(channel, (event, ...args) => {
      try {
        act(sender(event), ...args);
      } catch {
        // A message from an untrusted page is dropped.
      }
    });
  const handle = (channel, act) =>
    ipcMain.handle(channel, (event, ...args) => act(sender(event), ...args));
  handle("window:restore-drag", (event, phase, height) => restoreDrag(event.window, phase, height));
  on("window:minimize", ({ window }) => window.minimize());
  on("window:toggle-maximize", ({ window }) =>
    window.isMaximized() ? window.unmaximize() : window.maximize(),
  );
  on("window:close", ({ window }) => window.close());
  on("window:toggle-fullscreen", ({ window }) => window.setFullScreen(!window.isFullScreen()));
  // A remote page's settings belong to its server, which this app cannot restart.
  on("app:restart", ({ remote }) => {
    if (!remote) restart();
  });
  // This device's settings are one file whichever server a window shows; a change reaches the other windows.
  ipcMain.on("device:values", (event) => {
    try {
      sender(event);
      event.returnValue = device.read();
    } catch {
      event.returnValue = {};
    }
  });
  on("device:set", ({ window }, key, value) => {
    if (!device.set(key, value)) return;
    for (const other of BrowserWindow.getAllWindows())
      if (other !== window && trusted.has(other.webContents))
        other.webContents.send("device:changed", device.read());
  });
  handle("window:is-maximized", ({ window }) => window.isMaximized());
  handle("window:is-fullscreen", ({ window }) => window.isFullScreen());
  handle("app:version", () => app.getVersion());
  // Only the native dialog: the page sends the chosen path to the backend itself. Resolves to the absolute path of one
  // file, null when the dialog is cancelled. `kind` is `video` or `overlay`; an overlay's `extensions` (with their
  // dots, compound ones like `.tar.gz` included) filter the dialog, ahead of All Files, where a filter can state them. Only the local
  // backend's page may browse this machine's files.
  handle("workspace:pick-file", async ({ remote, window }, kind, extensions) => {
    if (remote) throw new Error("Not allowed");
    if (kind !== "video" && kind !== "overlay")
      throw new Error("Expected a kind of file: video or overlay");
    if (
      kind === "overlay" &&
      !(
        Array.isArray(extensions) &&
        extensions.length <= MAX_EXTENSIONS &&
        extensions.every((one) => typeof one === "string" && one.length <= MAX_EXTENSION_LENGTH)
      )
    )
      throw new Error("Expected a list of file extensions");
    const chosen = await dialog.showOpenDialog(window, {
      title: kind === "video" ? "Select Video File" : "Select Overlay File",
      properties: ["openFile"],
      filters:
        kind === "video" ? fileFilters("Video", VIDEO_EXTENSIONS) : fileFilters("Data", extensions),
    });
    return chosen.canceled ? null : chosen.filePaths[0];
  });
  // The absolute path an export is saved to, with the extension the export writes; null when cancelled. The
  // backend, which writes the file, checks the path again. Only the local backend's page may browse this machine.
  handle("export:pick-file", async ({ remote, window }, name, extension) => {
    if (remote) throw new Error("Not allowed");
    if (typeof name !== "string" || !Object.hasOwn(EXPORT_FILTERS, extension))
      throw new Error("Expected a file name and an export extension");
    const chosen = await dialog.showSaveDialog(window, {
      title: extension === "csv" ? "Export CSV" : "Export Video",
      defaultPath: path.basename(name),
      filters: [{ name: EXPORT_FILTERS[extension], extensions: [extension] }],
      properties: ["showOverwriteConfirmation"],
    });
    return suffixed(window, chosen, `.${extension}`);
  });
  // The absolute path of a workspace file to open, null when the dialog is cancelled. Only the local backend's page
  // may browse this machine's files.
  handle("workspace:pick-open", async ({ remote, window }) => {
    if (remote) throw new Error("Not allowed");
    const chosen = await dialog.showOpenDialog(window, {
      title: "Open Workspace",
      properties: ["openFile"],
      filters: [{ name: "Workspaces (*.ax-devil.workspace)", extensions: ["workspace"] }],
    });
    return chosen.canceled ? null : chosen.filePaths[0];
  });
  // The absolute path a workspace is saved to, null when cancelled. `name` is the suggested file name, cleaned of path
  // separators and control characters; `currentPath` (the workspace's file, when it has one) is the default instead.
  // The suffix is appended unless the chosen name has it; only an appended name that already exists asks first.
  handle("workspace:pick-save", async ({ remote, window }, name, currentPath) => {
    if (remote) throw new Error("Not allowed");
    if (
      typeof name !== "string" ||
      (currentPath !== undefined && currentPath !== null && typeof currentPath !== "string")
    )
      throw new Error("Expected a workspace name and an optional current path");
    const stem = name.replace(/[/\\\p{Cc}]/gu, "-");
    const file = /^\.*$/.test(stem) ? "-" : stem;
    const chosen = await dialog.showSaveDialog(window, {
      title: "Save Workspace As",
      filters: [
        { name: "Workspaces (*.ax-devil.workspace)", extensions: [WORKSPACE_SUFFIX.slice(1)] },
      ],
      defaultPath: currentPath || path.join(app.getPath("home"), `${file}${WORKSPACE_SUFFIX}`),
      properties: ["showOverwriteConfirmation"],
    });
    return suffixed(window, chosen, WORKSPACE_SUFFIX);
  });
  // The chosen absolute path, null when the dialog is cancelled; the page saves it through the settings. `title`
  // names what is chosen. Only the local backend's page may browse this machine's folders.
  handle("settings:pick-folder", async ({ remote, window }, title) => {
    if (remote) throw new Error("Not allowed");
    if (title !== undefined && typeof title !== "string") throw new Error("Expected a title");
    const chosen = await dialog.showOpenDialog(window, { title, properties: ["openDirectory"] });
    return chosen.canceled ? null : chosen.filePaths[0];
  });
  // Opens the folder of the log file the backend told the page, in the system's file manager. Only the local
  // backend's page may, and only for the one file name the backend writes its log to.
  handle("logs:open", async ({ remote }, file) => {
    if (remote) throw new Error("Not allowed");
    if (typeof file !== "string" || !path.isAbsolute(file) || path.basename(file) !== LOG_FILE_NAME)
      throw new Error("Expected the path of the log file");
    const failure = await shell.openPath(path.dirname(file));
    if (failure) throw new Error(failure);
  });
  handle("window:connect", ({ remote, window }, target, password) => {
    if (remote) throw new Error("Not allowed");
    if (typeof target !== "string" || (password !== undefined && typeof password !== "string"))
      throw new Error("Expected a server address and an optional password");
    return connect(window, target, password);
  });
  handle("window:disconnect", ({ remote, window }) => {
    if (!remote) throw new Error("Not allowed");
    return disconnect(window);
  });
}

module.exports = { installIpc, localWindows, openWindow };
