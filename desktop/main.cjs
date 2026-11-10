// The desktop shell's lifecycle: which windows exist and how they move between the local backend and another
// machine's server. Backend, window, link and remote details live in their own modules.
const { app, BrowserWindow, dialog, screen } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const { backendArgs, parseLaunch, relaunchArgs, userArgs } = require("./args.cjs");
const { backendExecutable, createBackend } = require("./backend.cjs");
const { handOff } = require("./hand-off.cjs");
const { confirmLink, linkTarget, registerLinks, singleInstance } = require("./links.cjs");
const { askAfterFailure, explainFailure, parseTarget, verifyServer } = require("./remote.cjs");
const { installUncaughtHandler } = require("./uncaught.cjs");
const { installIpc, localWindows, openWindow } = require("./window.cjs");
const { windowBounds } = require("./window-state.cjs");

const smoke = process.argv.includes("--smoke-test");
// The smoke test always starts its own backend and never attaches to a running session.
const port = smoke ? "0" : process.env.PORT || "7330";
const devUrl = smoke ? undefined : process.env.DESKTOP_DEV_URL;
// The backend starts the hidden renderer with this executable and folder; in a packaged app it cannot find them itself.
process.env.AX_DEVIL_ELECTRON ??= process.execPath;
process.env.AX_DEVIL_DESKTOP ??= app.getAppPath();
// The video canvas needs WebGL2, which a machine without a GPU (a CI runner, a virtual display) blocklists. On Linux and
// Windows the smoke test draws with SwiftShader, Chromium's software renderer, so its result does not depend on the
// machine's graphics. SwiftShader does not start on a macOS runner, whose own GPU path (Metal) works.
if (smoke && process.platform !== "darwin") {
  for (const [name, value] of [
    ["use-gl", "angle"],
    ["use-angle", "swiftshader"],
    ["enable-unsafe-swiftshader"],
    ["ignore-gpu-blocklist"],
  ])
    app.commandLine.appendSwitch(name, value);
}
// A second window hidden behind the first, as the sharing scenario makes, is "occluded" on macOS and Windows: its page
// stops drawing and the frames and screenshots the smoke test waits for never come. Nobody watches the smoke test.
if (smoke) {
  app.commandLine.appendSwitch(
    "disable-features",
    "CalculateNativeWinOcclusion,MacWebContentsOcclusion",
  );
  app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");
  app.commandLine.appendSwitch("disable-renderer-backgrounding");
  // AX_DEVIL_SMOKE_DPR=2 runs the smoke test on a high-density screen.
  if (process.env.AX_DEVIL_SMOKE_DPR)
    app.commandLine.appendSwitch("force-device-scale-factor", process.env.AX_DEVIL_SMOKE_DPR);
  // AX_DEVIL_SMOKE_SLOWDOWN=4 runs every page's scripts four times slower, as on a loaded CI runner, to find a smoke
  // check that clicks or measures before the layout has settled.
  const slowdown = Number(process.env.AX_DEVIL_SMOKE_SLOWDOWN);
  if (slowdown > 1)
    app.on("web-contents-created", (_event, contents) => {
      contents.debugger.attach("1.3");
      // A window that closes first has nothing left to slow.
      contents.debugger
        .sendCommand("Emulation.setCPUThrottlingRate", { rate: slowdown })
        .catch(() => undefined);
    });
}
// Electron's own data (Chromium profile, crash dumps) is browser state, not ax-devil data: the backend owns the
// app's folders, so keep Electron out of them.
app.setPath("userData", path.join(app.getPath("cache"), "ax-devil-chromium"));
// The first local window of this run is remembered: where it was and how big, as the backend keeps it.
let localOrigin;
let remembered;
let windowState;
let stopping = false;
let stopped = false;
let exitCode = 0;
// The smoke test's verdict, for a caller that cannot rely on this GUI program's output or exit code: the file named by
// AX_DEVIL_SMOKE_RESULT gets PASS or FAIL: and the reason, the first time only. It appears whole or not at all.
let reported = false;
function report(verdict) {
  const file = process.env.AX_DEVIL_SMOKE_RESULT;
  if (!smoke || !file || reported) return;
  reported = true;
  try {
    fs.writeFileSync(`${file}.partial`, `${verdict.replace(/\s+/g, " ")}\n`);
    fs.renameSync(`${file}.partial`, file);
  } catch (error) {
    console.error(`Could not write the smoke result: ${error.message}`);
  }
}
// Opening a remote window that may fail and asking what to do then; the app outlives its last window meanwhile.
let recovering = 0;

// What the command line asks for: a server to open, and the rest goes to the local backend. A link or `--server`
// must not reach the backend, which would take its address for the command.
let commandLine = { launch: {}, rest: [] };
try {
  commandLine = parseLaunch(userArgs(process.argv, process.defaultApp));
} catch (error) {
  fail(error);
}

const backend = createBackend({
  port,
  devUrl,
  executable: backendExecutable(app),
  args: [
    // The smoke test's sharing listener takes a free loopback port, so no other machine or test can be in the way.
    ...(smoke ? ["--share-address", "127.0.0.1:0"] : []),
    ...backendArgs(commandLine.rest),
  ],
  onUnexpectedExit: (error) => {
    if (localWindows().length > 0) fail(error);
  },
});

function fail(error) {
  console.error(error);
  report(`FAIL: ${error?.message ?? error}`);
  exitCode = 1;
  // Already quitting: the dialog would only report that quitting stopped what was starting.
  if (stopping) return;
  if (!smoke) dialog.showErrorBox("ax-devil could not continue", String(error));
  app.quit();
}

// A failure nobody caught would leave the smoke test waiting for its own timeout; the app tells the user and goes on.
if (smoke) process.on("uncaughtException", fail);
else installUncaughtHandler({ app, dialog });

// `window` takes the place of `replace`, which closes.
function adopt(window, replace) {
  if (replace && !replace.isDestroyed()) {
    window.setBounds(replace.getBounds());
    replace.close();
  }
  return window;
}

// The saved window, or nothing when there is none or the backend does not answer; the window then opens at defaults.
async function loadWindowState() {
  try {
    const response = await fetch(`${localOrigin}/api/windows/main`, {
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
    await fetch(`${localOrigin}/api/windows/main`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(windowState),
      signal: AbortSignal.timeout(1000),
    });
  } catch (error) {
    console.error(`Could not save the window position: ${error.message}`);
  }
}

// Keeps `window`'s position for the next start. The normal bounds are kept apart from the maximized state, so a
// maximized window restores to its own size.
function remember(window) {
  const update = () => {
    if (!window.isDestroyed())
      windowState = { ...window.getNormalBounds(), maximized: window.isMaximized() };
  };
  for (const event of ["resize", "move", "maximize", "unmaximize", "close"])
    window.on(event, update);
}

// Opens a window on the local backend, starting it when it is not running yet. The first one of a run opens where
// it was last closed; the smoke test always opens at the defaults.
async function showLocal(replace) {
  localOrigin = await backend.ensure();
  const origin = devUrl || localOrigin;
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw new Error("Invalid local desktop URL");
  const first = !replace && !smoke && !remembered;
  const saved = first ? await loadWindowState() : undefined;
  const bounds = windowBounds(
    saved,
    screen.getAllDisplays().map((display) => display.workArea),
  );
  const window = adopt(
    await openWindow(origin, { fatal: fail, bounds, restartable: backend.owned() }),
    replace,
  );
  if (first) {
    remembered = true;
    if (saved?.maximized) window.maximize();
    remember(window);
  }
  return window;
}

// Opens a window on another machine's server (a server address or a join link) with no local backend involved.
// A failure asks what to do; resolves to the window, or to nothing when the user chose to quit. Returning to local
// focuses a local window, or opens one when only remote windows remain.
async function showRemote(target, password, replace) {
  const remote = parseTarget(target, password);
  // A page that fails to load destroys its window, which may be the last one; that must not quit the app.
  recovering++;
  try {
    for (;;) {
      let reason;
      try {
        await verifyServer(remote.base);
        const window = await openWindow(remote.origin, {
          url: remote.url,
          remote: true,
          fatal: fail,
        });
        watchRemote(window, remote);
        return adopt(window, replace);
      } catch (error) {
        reason = error.message;
      }
      const choice = await askAfterFailure(replace, reason);
      if (choice === "retry") continue;
      if (choice === "quit") return app.quit();
      const [local] = localWindows();
      if (local) return local.focus();
      return showLocal();
    }
  } finally {
    recovering--;
  }
}

// A remote page that stops loading after it opened gets the same way out as one that never opened.
function watchRemote(window, remote) {
  let asking = false;
  window.webContents.on("did-fail-load", async (_event, code, description, _url, isMainFrame) => {
    // -3 is a navigation that was cancelled, such as one this window refused.
    if (!isMainFrame || code === -3 || asking || window.isDestroyed()) return;
    asking = true;
    const choice = await askAfterFailure(
      window,
      `Lost ${remote.base}: ${explainFailure(description)}`,
    );
    asking = false;
    if (window.isDestroyed()) return;
    if (choice === "retry") window.loadURL(remote.url).catch(() => {});
    else if (choice === "quit") app.quit();
    else showLocal(window).catch(fail);
  });
}

// Opens what a launch asks for: its server, its confirmed link, or nothing.
async function openLaunch({ server, link }) {
  if (server) return showRemote(server);
  if (!link) return undefined;
  const target = linkTarget(link);
  return (await confirmLink(target)) ? showRemote(target) : undefined;
}

// A launch that reaches the running app: it opens a window (a command's, a server's), or raises one when it asks for
// nothing. A command's items go to the local backend first, which the new local window then starts from.
async function onLaunch(launch) {
  await app.whenReady();
  try {
    if (launch.command) {
      await handOff(backendExecutable(app), await backend.ensure(), launch.command, launch.cwd);
      await showLocal();
    } else if (!(await openLaunch(launch)) && !launch.server && !launch.link) {
      const [existing] = BrowserWindow.getAllWindows();
      if (existing) {
        if (existing.isMinimized()) existing.restore();
        existing.focus();
      } else await showLocal();
    }
  } catch (error) {
    dialog.showErrorBox(
      launch.command ? "ax-devil could not open that" : "ax-devil could not open the server",
      error.message,
    );
  }
}

installIpc({
  connect: (window, target, password) => {
    // Resolves the address now so a bad one rejects in the page; the rest answers through native dialogs.
    parseTarget(target, password);
    showRemote(target, password, window).catch(fail);
  },
  disconnect: (window) => {
    showLocal(window).catch(fail);
  },
  // Quitting stops the backend first (`before-quit`); the new process starts when this one has exited.
  restart: () => {
    app.relaunch({ args: relaunchArgs(process.argv, process.defaultApp) });
    app.quit();
  },
});

app.on("before-quit", (event) => {
  if (stopped) return;
  event.preventDefault();
  if (stopping) return;
  stopping = true;
  void saveWindowState()
    .then(() => backend.stop())
    .then(() => {
      stopped = true;
      app.exit(exitCode);
    });
});
app.on("window-all-closed", () => {
  if (recovering === 0) app.quit();
});
process.on("SIGINT", () => app.quit());
process.on("SIGTERM", () => app.quit());

// A later launch of the app, and a development shell, each run on their own.
if (!smoke && !devUrl && !singleInstance(onLaunch)) app.quit();
else
  app
    .whenReady()
    .then(async () => {
      if (smoke) {
        const window = await showLocal();
        const smokeTest = require("./smoke.cjs");
        await smokeTest(window, { onLaunch, openLaunch, showLocal, showRemote });
        report("PASS");
        app.quit();
        return;
      }
      registerLinks((error) => console.error(error.message));
      if (!(await openLaunch(commandLine.launch)) && !stopping) await showLocal();
    })
    .catch(fail);
