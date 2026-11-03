// Windows and the IPC they may use. A window trusts exactly one origin, fixed when it is created: the page there
// may use the shell's IPC, navigation to any other origin is blocked, and new windows are denied. The one link a page
// may open is the project's, in the default browser.
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const path = require("node:path");

const { reserved } = require("./generated/api.json");
const { windowBounds } = require("./window-state.cjs");

// Video files the Add Video dialog offers; the backend's intake takes any path, the viewer judges the media.
const VIDEO_EXTENSIONS = ["mp4", "avi", "mkv", "mov"];

// What an export writes, by file extension; the page names one of these.
const EXPORT_EXTENSIONS = ["mp4", "png"];

// Zoom keys the shell handles itself (the backend refuses them as shortcuts), by the key that triggers them.
const ZOOM_STEPS = { "=": 0.5, "+": 0.5, "-": -0.5, 0: null };

// The project link the About dialog offers (the page and this constant must agree).
const PROJECT_LINK = "https://github.com/rasmusrynell/ax-devil";

// Window contents -> { origin, remote }; `remote` is a page served by another machine's backend.
const trusted = new WeakMap();

/**
 * Opens a window on `origin` and resolves once its page has loaded; `url` (default: the origin) is what it loads.
 * `bounds` is its position and size (default: the standard size). A failed load destroys the window and rejects.
 */
async function openWindow(
  origin,
  { url = origin, remote = false, fatal, bounds = windowBounds(undefined, []) },
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
    },
  });
  const contents = window.webContents;
  trusted.set(contents, { origin, remote });
  const sendMaximized = () => contents.send("window:maximized", window.isMaximized());
  window.on("maximize", sendMaximized);
  window.on("unmaximize", sendMaximized);
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
 * Registers every IPC handler, each answering only its own window's trusted page. `connect` is honoured from a
 * local window and `disconnect` from a remote one, by the callbacks given here.
 */
function installIpc({ connect, disconnect }) {
  const on = (channel, act) =>
    ipcMain.on(channel, (event) => {
      try {
        act(sender(event));
      } catch {
        // A message from an untrusted page is dropped.
      }
    });
  const handle = (channel, act) =>
    ipcMain.handle(channel, (event, ...args) => act(sender(event), ...args));
  on("window:minimize", ({ window }) => window.minimize());
  on("window:toggle-maximize", ({ window }) =>
    window.isMaximized() ? window.unmaximize() : window.maximize(),
  );
  on("window:close", ({ window }) => window.close());
  on("window:toggle-fullscreen", ({ window }) => window.setFullScreen(!window.isFullScreen()));
  handle("window:is-maximized", ({ window }) => window.isMaximized());
  handle("app:version", () => app.getVersion());
  // Only the native dialog: the page sends the chosen paths to the backend itself. Resolves to the absolute paths,
  // none when the dialog is cancelled. Only the local backend's page may browse this machine's files.
  handle("workspace:pick-videos", async ({ remote, window }) => {
    if (remote) throw new Error("Not allowed");
    const chosen = await dialog.showOpenDialog(window, {
      title: "Add Video",
      properties: ["openFile", "multiSelections"],
      filters: [{ name: "Video files", extensions: VIDEO_EXTENSIONS }],
    });
    return chosen.canceled ? [] : chosen.filePaths;
  });
  // The absolute path an export is saved to, with the extension the export writes; null when cancelled. The
  // backend, which writes the file, checks the path again. Only the local backend's page may browse this machine.
  handle("export:pick-file", async ({ remote, window }, name, extension) => {
    if (remote) throw new Error("Not allowed");
    if (typeof name !== "string" || !EXPORT_EXTENSIONS.includes(extension))
      throw new Error("Expected a file name and an export extension");
    const chosen = await dialog.showSaveDialog(window, {
      title: "Export",
      defaultPath: path.basename(name),
      filters: [{ name: extension.toUpperCase(), extensions: [extension] }],
    });
    if (chosen.canceled) return null;
    return chosen.filePath.toLowerCase().endsWith(`.${extension}`)
      ? chosen.filePath
      : `${chosen.filePath}.${extension}`;
  });
  // The chosen absolute path, null when the dialog is cancelled; the page saves it through the settings. Only the
  // local backend's page may browse this machine's folders.
  handle("settings:pick-folder", async ({ remote, window }) => {
    if (remote) throw new Error("Not allowed");
    const chosen = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
    return chosen.canceled ? null : chosen.filePaths[0];
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
