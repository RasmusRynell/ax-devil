const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const path = require("node:path");

const { apiVersion } = require("./generated/api.json");

const smoke = process.argv.includes("--smoke-test");
// The smoke test always starts its own release backend and never attaches to a running session.
const port = smoke ? "0" : process.env.PORT || "7330";
const devUrl = smoke ? undefined : process.env.DESKTOP_DEV_URL;
// Electron's own data (Chromium profile, crash dumps) is browser state, not ax-devil data: the backend owns the
// app's folders, so keep Electron out of them.
app.setPath("userData", path.join(app.getPath("cache"), "ax-devil-chromium"));
let backend;
let stopping = false;
let stopped = false;
let exitCode = 0;

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
      return `http://127.0.0.1:${port}`;
    } catch (error) {
      if (error.cause?.code !== "ECONNREFUSED") throw error;
    }
  }
  const executable = path.resolve(__dirname, "../target/release/ax-devil");
  backend = spawn(executable, [], {
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

app.on("before-quit", (event) => {
  if (stopped) return;
  event.preventDefault();
  if (stopping) return;
  stopping = true;
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
});
app.on("window-all-closed", () => app.quit());
process.on("SIGINT", () => app.quit());
process.on("SIGTERM", () => app.quit());

app
  .whenReady()
  .then(async () => {
    const backendOrigin = await startBackend();
    const origin = devUrl || backendOrigin;
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw new Error("Invalid local desktop URL");
    const window = new BrowserWindow({
      title: "ax-devil",
      width: 1280,
      height: 800,
      minWidth: 640,
      minHeight: 480,
      autoHideMenuBar: true,
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
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
