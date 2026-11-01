const { app, BrowserWindow, dialog, ipcMain, screen } = require("electron");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const path = require("node:path");

const { backendArgs, userArgs } = require("./args.cjs");
const { apiVersion, reserved } = require("./generated/api.json");
const { windowBounds } = require("./window-state.cjs");

const smoke = process.argv.includes("--smoke-test");
// The smoke test always starts its own release backend and never attaches to a running session.
const port = smoke ? "0" : process.env.PORT || "7330";
const devUrl = smoke ? undefined : process.env.DESKTOP_DEV_URL;
// The video canvas needs WebGL2, which a machine without a GPU (a CI runner, a virtual display) blocklists. The smoke
// test draws with SwiftShader, Chromium's software renderer, so its result does not depend on the machine's graphics.
if (smoke) {
  for (const [name, value] of [
    ["use-gl", "angle"],
    ["use-angle", "swiftshader"],
    ["enable-unsafe-swiftshader"],
    ["ignore-gpu-blocklist"],
  ])
    app.commandLine.appendSwitch(name, value);
}
// Video files the Add Video dialog offers; the backend's intake takes any path, the viewer judges the media.
const VIDEO_EXTENSIONS = ["mp4", "avi", "mkv", "mov"];
// Electron's own data (Chromium profile, crash dumps) is browser state, not ax-devil data: the backend owns the
// app's folders, so keep Electron out of them.
app.setPath("userData", path.join(app.getPath("cache"), "ax-devil-chromium"));
// Zoom keys the app handles itself (the backend refuses them as shortcuts), by the key that triggers them.
const ZOOM_STEPS = { "=": 0.5, "+": 0.5, "-": -0.5, 0: null };
let backend;
let backendOrigin;
let windowState;
let stopping = false;
let stopped = false;
let exitCode = 0;
const args = backendArgs(userArgs(process.argv, process.defaultApp));

// Reuses a compatible backend already on the port; otherwise starts the release build and owns it.
async function startBackend() {
  if (port !== "0") {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/info`, {
        signal: AbortSignal.timeout(1500),
      });
      const info = await response.json();
      if (info.app !== "ax-devil" || info.api !== apiVersion)
        throw new Error(`Port ${port} belongs to an incompatible server.`);
      // A development backend serves no frontend; only the development supervisor pairs it with one.
      if (info.development && !devUrl)
        throw new Error(
          `Port ${port} runs a development backend; use make desktop or another PORT.`,
        );
      if (args.length)
        console.error(`Ignoring the command line: the backend on port ${port} is already running.`);
      return `http://127.0.0.1:${port}`;
    } catch (error) {
      if (error.cause?.code !== "ECONNREFUSED") throw error;
    }
  }
  const executable = path.resolve(__dirname, "../target/release/ax-devil");
  backend = spawn(executable, args, {
    env: { ...process.env, PORT: port },
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stderr.on("data", (data) => process.stderr.write(data));
  backend.on("exit", (code, signal) => {
    if (!stopping) fail(new Error(`Backend exited unexpectedly: ${code ?? signal}`));
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Backend startup timed out")), 10000);
    backend.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    backend.once("exit", () => {
      clearTimeout(timer);
      reject(new Error("Backend stopped during startup"));
    });
    createInterface({ input: backend.stdout }).on("line", (line) => {
      const match = /^ax-devil: (http:\/\/127\.0\.0\.1:\d+)$/.exec(line);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
}

function fail(error) {
  console.error(error);
  exitCode = 1;
  if (!smoke) dialog.showErrorBox("ax-devil could not continue", String(error));
  app.quit();
}

// The saved window, or nothing when there is none or the backend does not answer; the window then opens at defaults.
async function loadWindowState() {
  try {
    const response = await fetch(`${backendOrigin}/api/windows/main`, {
      signal: AbortSignal.timeout(1000),
    });
    return response.status === 200 ? await response.json() : undefined;
  } catch {
    return undefined;
  }
}

// Saves the window before the backend stops; a failure only costs the next start its position.
async function saveWindowState() {
  if (!windowState) return;
  try {
    await fetch(`${backendOrigin}/api/windows/main`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(windowState),
      signal: AbortSignal.timeout(1000),
    });
  } catch (error) {
    console.error(`Could not save the window position: ${error.message}`);
  }
}

app.on("before-quit", (event) => {
  if (stopped) return;
  event.preventDefault();
  if (stopping) return;
  stopping = true;
  void saveWindowState().then(stopBackend);
});

function stopBackend() {
  const finish = () => {
    stopped = true;
    app.exit(exitCode);
  };
  if (!backend || backend.exitCode !== null || backend.signalCode !== null) return finish();
  const timer = setTimeout(() => backend.kill("SIGKILL"), 3000);
  backend.once("exit", () => {
    clearTimeout(timer);
    finish();
  });
  backend.kill("SIGINT");
}
app.on("window-all-closed", () => app.quit());
process.on("SIGINT", () => app.quit());
process.on("SIGTERM", () => app.quit());

app
  .whenReady()
  .then(async () => {
    backendOrigin = await startBackend();
    const origin = devUrl || backendOrigin;
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw new Error("Invalid local desktop URL");
    // The smoke test screenshots the default window, whatever an earlier run left.
    const saved = smoke ? undefined : await loadWindowState();
    const window = new BrowserWindow({
      title: "ax-devil",
      ...windowBounds(
        saved,
        screen.getAllDisplays().map((display) => display.workArea),
      ),
      minWidth: 640,
      minHeight: 480,
      // The page draws its own title bar and window controls (see preload.cjs).
      frame: false,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        preload: path.join(__dirname, "preload.cjs"),
      },
    });
    if (saved?.maximized) window.maximize();
    // The normal bounds are kept apart from the maximized state, so a maximized window restores to its own size.
    const remember = () => {
      if (!smoke && !window.isDestroyed())
        windowState = { ...window.getNormalBounds(), maximized: window.isMaximized() };
    };
    for (const event of ["resize", "move", "maximize", "unmaximize", "close"])
      window.on(event, remember);
    const sendMaximized = () => window.webContents.send("window:maximized", window.isMaximized());
    window.on("maximize", sendMaximized);
    window.on("unmaximize", sendMaximized);
    ipcMain.on("window:minimize", () => window.minimize());
    ipcMain.on("window:toggle-maximize", () =>
      window.isMaximized() ? window.unmaximize() : window.maximize(),
    );
    ipcMain.on("window:close", () => window.close());
    ipcMain.handle("window:is-maximized", () => window.isMaximized());
    // Only the native dialog: the page sends the chosen paths to the backend itself. Resolves to the absolute paths,
    // none when the dialog is cancelled.
    ipcMain.handle("workspace:pick-videos", async () => {
      const chosen = await dialog.showOpenDialog(window, {
        title: "Add Video",
        properties: ["openFile", "multiSelections"],
        filters: [{ name: "Video files", extensions: VIDEO_EXTENSIONS }],
      });
      return chosen.canceled ? [] : chosen.filePaths;
    });
    // The chosen absolute path, null when the dialog is cancelled; the page saves it through the settings.
    ipcMain.handle("settings:pick-folder", async () => {
      const chosen = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
      return chosen.canceled ? null : chosen.filePaths[0];
    });
    // There is no application menu, so zoom is handled here; Chromium remembers it per host.
    window.webContents.on("before-input-event", (event, input) => {
      if (input.type !== "keyDown" || !input.control || input.alt) return;
      const step = ZOOM_STEPS[input.key];
      if (step === undefined || !reserved.includes(`Ctrl+${input.key}`)) return;
      const zoom = window.webContents;
      zoom.setZoomLevel(step === null ? 0 : zoom.getZoomLevel() + step);
      event.preventDefault();
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (new URL(url).origin !== origin) event.preventDefault();
    });
    window.webContents.on("render-process-gone", (_event, details) =>
      fail(new Error(`Renderer stopped: ${details.reason}`)),
    );
    window.on("unresponsive", () => fail(new Error("Desktop window stopped responding")));
    await window.loadURL(origin);
    if (smoke) {
      const timer = setTimeout(() => fail(new Error("Desktop smoke test timed out")), 30000);
      await require("./smoke.cjs")(window);
      clearTimeout(timer);
      window.close();
    }
  })
  .catch(fail);
