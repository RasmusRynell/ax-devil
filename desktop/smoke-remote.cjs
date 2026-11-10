// The smoke test's remote-server scenario: the app on another machine's server, and the shell's refusals.
const { BrowserWindow, app, dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { backendExecutable, startProcess } = require("./backend.cjs");
const { apiVersion, shellApi } = require("./generated/api.json");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  SMOKE_ROOT,
  frames,
  openAddVideo,
  page,
  setView,
  until,
} = require("./smoke-kit.cjs");

const PASSWORD = "smoke-password";
const FIXTURE = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");
const OVERLAY = path.join(SMOKE_ROOT, "fixtures/datasets/mot/test/MINI-02/det/det.txt");

// A server that answers `/api/info` only, as a `shell` interface version: another one than this app's refuses it
// before any page loads. With `dropPages` every other request is cut off, as a server that disconnects after
// answering the check.
async function fakeServer(requests, shell, dropPages = false) {
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    if (request.url !== "/api/info" && dropPages) return request.socket.destroy();
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ app: "ax-devil", api: apiVersion, version: "9.9.9", shell }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

// Answers the shell's next native dialogs with the given button indexes (then Cancel or Return to local),
// recording what each one asked.
function stubDialogs(...answers) {
  const original = dialog.showMessageBox.bind(dialog);
  const asked = [];
  dialog.showMessageBox = async (...args) => {
    asked.push(args.at(-1));
    return { response: answers.shift() ?? 1 };
  };
  return { asked, restore: () => (dialog.showMessageBox = original) };
}

// The app on another machine's server: a release `serve` with a known password, then a server this shell cannot use.
async function remote(local, shell) {
  const localOrigin = new URL(local.webContents.getURL()).origin;
  const served = await startProcess(
    backendExecutable(app),
    ["serve", "--port", "0"],
    { AX_DEVIL_SERVER_PASSWORD: PASSWORD },
    () => {},
  );
  const staleRequests = [];
  const stale = await fakeServer(staleRequests, shellApi + 1);
  const droppedRequests = [];
  const dropping = await fakeServer(droppedRequests, shellApi, true);
  const stubs = [];
  try {
    const windowAt = (origin) => () =>
      BrowserWindow.getAllWindows().find(
        (w) => !w.isDestroyed() && w.webContents.getURL().startsWith(origin),
      );
    // The local page connects: its window is replaced by one on the server, with the password passed as a fragment.
    await page(local).js(
      `window.axDevilWindow.connect(${JSON.stringify(served.origin)}, ${JSON.stringify(PASSWORD)})`,
    );
    const window = await until(windowAt(served.origin), "a window on the server");
    const { js, wait } = page(window);
    // The server's own embedded frontend booted and shows the app for the password it was given.
    await wait(
      "document.title === 'Untitled — ax-devil' && document.querySelector('h1')?.textContent === 'ax-devil'",
    );
    assert.equal(await js("location.origin"), served.origin);
    await until(() => local.isDestroyed(), "the local window to close");
    for (const theme of ["light", "dark"]) {
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
      await fs.writeFile(
        path.join(SCREENSHOTS, `remote-${theme}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
    }

    // The window may not browse this machine's files, so Add Video asks for a path on the server instead. The
    // server's first-run Quick Setup comes first when its settings have not seen it.
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done')?.click()",
    );
    await wait("!document.querySelector('dialog')");
    await js(
      "!document.querySelector('[aria-label=\"Workspace items\"], [data-cursor-id=\"sidebar:add-video\"]') && document.querySelector('[aria-label=Workspace]').click()",
    );
    await frames({ js });
    await openAddVideo(window);
    await wait("document.querySelector('dialog[open] input')");
    // With a blank path, Add is disabled and says why; the dialog still fits the smallest window at 200% zoom.
    await wait(
      "document.querySelector('dialog[open] [role=alert]') && [...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Add').disabled",
    );
    const bounds = window.getBounds();
    for (const [theme, zoom] of [
      ["light", 1],
      ["dark", 2],
    ]) {
      await setView(window, [640, 480], zoom);
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames({ js });
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `Add Video must fit the 640x480 window at ${zoom * 100}% zoom with a failure showing`,
      );
    }
    window.webContents.setZoomFactor(1);
    window.setBounds(bounds);
    window.webContents.insertText(FIXTURE);
    await wait(`document.querySelector('dialog[open] input').value === ${JSON.stringify(FIXTURE)}`);
    for (const theme of ["light", "dark"]) {
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames({ js });
      await fs.writeFile(
        path.join(SCREENSHOTS, `remote-add-video-${theme}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
    }

    // An overlay path that selects MOT Annotations brings in that decoder's options: the dialog fits the smallest
    // window at 200% zoom in both themes. The overlay is cleared again for the Add below.
    const size = window.getSize();
    const overlay =
      "[...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === 'Overlay path').control";
    await wait(`!!${overlay}`);
    await js(`${overlay}.focus()`);
    window.webContents.insertText(OVERLAY);
    await wait(
      `${overlay}.value === ${JSON.stringify(OVERLAY)} && /Image height/.test(document.querySelector('dialog[open]').innerText)`,
    );
    for (const [theme, zoom] of [
      ["light", 1],
      ["dark", 1],
      ["light", 2],
      ["dark", 2],
    ]) {
      await setView(window, [640, 480], zoom);
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames({ js });
      await fs.writeFile(
        path.join(SCREENSHOTS, `add-video-options-${theme}${zoom === 1 ? "" : "-200"}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
      // A folder box scrolls its own long path, which the check reports as text cut off with no name.
      assert.deepEqual(
        (await js(`(${fit.dialog.toString()})()`)).filter((problem) => problem !== 'cut off: ""'),
        [],
        `The Add video dialog with decoder options must fit the 640x480 window at ${zoom * 100}% zoom`,
      );
    }
    window.webContents.setZoomFactor(1);
    window.setSize(...size);
    await js(`(() => {
      const input = ${overlay};
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    // Add waits for the backend's word on the files typed.
    await wait(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Add').disabled === false",
    );
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Add').click()",
    );
    await wait("!document.querySelector('dialog') && document.querySelector('canvas')");

    // The server's page is not the local app: it cannot reconnect the window, and it cannot leave its origin.
    const refused = await js(
      "window.axDevilWindow.connect('http://127.0.0.1:1').then(() => 'accepted', (error) => error.message)",
    );
    assert.match(refused, /not allowed/i);
    const count = BrowserWindow.getAllWindows().length;
    const navigating = new Promise((resolve) => window.webContents.once("will-navigate", resolve));
    await js("setTimeout(() => { location.href = 'http://localhost:1/' })");
    assert.equal((await navigating).defaultPrevented, true);
    assert.equal(new URL(window.webContents.getURL()).origin, served.origin);
    assert.equal(BrowserWindow.getAllWindows().length, count);

    // An ax-devil:// link asks first. Cancelling opens nothing; accepting opens the server it names.
    const link = `ax-devil://open?url=${encodeURIComponent(`${served.origin}/#password=${PASSWORD}`)}`;
    const confirms = stubDialogs(1, 0);
    stubs.push(confirms);
    await shell.openLaunch({ link });
    assert.equal(BrowserWindow.getAllWindows().length, count);
    const linked = await shell.openLaunch({ link });
    assert.equal(BrowserWindow.getAllWindows().length, count + 1);
    assert.equal(confirms.asked[0].message, `Open session on ${new URL(served.origin).host}?`);
    assert.deepEqual(confirms.asked[0].buttons, ["Open", "Cancel"]);
    linked.close();
    confirms.restore();

    // A server whose frontend needs another shell interface is refused before any page loads, naming both versions;
    // Retry asks it again, and returning to local reaches the local backend even though a remote window stays open.
    const failures = stubDialogs(0, 1);
    stubs.push(failures);
    await shell.showRemote(stale.origin, undefined, window);
    assert.deepEqual(failures.asked[0].buttons, ["Retry", "Return to local", "Quit"]);
    assert.match(
      failures.asked[0].detail,
      new RegExp(`version ${shellApi + 1}.*version ${shellApi}`),
    );
    assert.deepEqual(staleRequests, ["/api/info", "/api/info"]);
    assert.equal(window.isDestroyed(), false);
    assert.ok(windowAt(localOrigin)(), "Return to local must reach the local backend");
    failures.restore();

    // A host that is not listening is said to be exactly that.
    const unreachable = stubDialogs(1);
    stubs.push(unreachable);
    const gone = http.createServer();
    await new Promise((resolve) => gone.listen(0, "127.0.0.1", resolve));
    const { port } = gone.address();
    await new Promise((resolve) => gone.close(resolve));
    await shell.showRemote(`http://127.0.0.1:${port}`);
    assert.match(unreachable.asked[0].detail, /nothing is listening there.*ECONNREFUSED/);
    unreachable.restore();

    // Disconnecting returns to the local backend, which was running all along; only a remote page may ask.
    await js("window.axDevilWindow.disconnect()");
    const back = await until(windowAt(localOrigin), "a window on the local backend");
    await until(() => window.isDestroyed(), "the remote window to close");
    assert.match(
      await page(back).js(
        "window.axDevilWindow.disconnect().then(() => 'accepted', (error) => error.message)",
      ),
      /not allowed/i,
    );

    // With no window left, a server that answers the check and then drops the page keeps the app alive to ask
    // again; only choosing Quit quits.
    const quits = [];
    const quit = app.quit.bind(app);
    app.quit = () => quits.push("quit");
    try {
      for (const open of BrowserWindow.getAllWindows()) open.destroy();
      await until(() => BrowserWindow.getAllWindows().length === 0, "every window to close");
      quits.length = 0;
      const dropped = stubDialogs(0, 2);
      stubs.push(dropped);
      await shell.showRemote(dropping.origin);
      assert.equal(dropped.asked.length, 2);
      assert.match(dropped.asked[0].detail, /ERR_/);
      assert.deepEqual(droppedRequests, ["/api/info", "/", "/api/info", "/"]);
      assert.equal(quits.length, 1, "Only Quit may quit while the server is being retried");
    } finally {
      app.quit = quit;
    }
  } finally {
    for (const stub of stubs) stub.restore();
    stale.server.close();
    dropping.server.close();
    served.child.kill("SIGINT");
  }
}

module.exports = { run: ({ window, shell }) => remote(window, shell) };
