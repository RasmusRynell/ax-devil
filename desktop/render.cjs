// The hidden renderer `ax-devil` starts for export and `catalog render`: Electron with no display, one offscreen
// window on the render page, and no way to reach anything but the backend's loopback socket the page opens itself.
// Start it as `electron <desktop folder> --render-host --ozone-platform=headless <graphics switches>` (start.cjs picks
// this file); the backend writes one line of
// JSON, `{ "port": N, "secret": "..." }`, to stdin, and the page gets both in the fragment of its URL, which is never
// sent or logged (see src/render_host.rs). The process exits when stdin closes, so it does not outlive the backend.
const { app, BrowserWindow, session } = require("electron");
const path = require("node:path");
const readline = require("node:readline");

// The page the frontend build writes (frontend/render.html).
const RENDER_PAGE = path.join(__dirname, "generated/render/render.html");

/** Ends the process with `message` on the error output, which the backend quotes in its own error. */
function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  app.exit(code);
}

/** Resolves with the backend's `{ port, secret }` line; keeps watching stdin and exits when it closes. */
function readHandshake() {
  return new Promise((resolve) => {
    const lines = readline.createInterface({ input: process.stdin });
    lines.once("line", (line) => {
      try {
        const { port, secret } = JSON.parse(line);
        if (!Number.isInteger(port) || typeof secret !== "string" || secret === "") {
          throw new Error("port or secret missing");
        }
        resolve({ port, secret });
      } catch (error) {
        fail(`The backend sent a handshake the render page cannot read: ${error.message}`, 2);
      }
    });
    lines.once("close", () => app.exit(0));
  });
}

async function main() {
  const handshake = readHandshake();
  await app.whenReady();
  const { port, secret } = await handshake;
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, allow) =>
    allow(false),
  );
  const window = new BrowserWindow({
    show: false,
    width: 1280,
    height: 720,
    webPreferences: {
      offscreen: true,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      // A hidden page would otherwise slow its timers and stop drawing.
      backgroundThrottling: false,
    },
  });
  const contents = window.webContents;
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event) => event.preventDefault());
  contents.on("render-process-gone", (_event, details) =>
    fail(`The render page stopped: ${details.reason}`),
  );
  app.on("child-process-gone", (_event, details) =>
    process.stderr.write(`A ${details.type} process stopped: ${details.reason}\n`),
  );
  await contents.loadFile(RENDER_PAGE, {
    hash: `port=${port}&secret=${encodeURIComponent(secret)}`,
  });
}

main().catch((error) => fail(`Cannot start the render page: ${error.message}`));
