// The smoke test's Plugins scenario, with the demo plugin installed: its view mounts and gets `context` over its port,
// its address opened as a page has the opaque origin, a frame loads nothing but views, a view that loads another
// page loses its port, and a view in a busy loop leaves the window responsive. Its command's dialog, opened from the
// Plugins menu over a video with a lane, fits the smallest window at 200% zoom in both themes.
const { BrowserWindow, nativeTheme, session } = require("electron");
const assert = require("node:assert/strict");
const path = require("node:path");
const fit = require("./fit.cjs");
const { inBackendWindow, VIDEO } = require("./smoke-twice.cjs");
const { frames, page, setView, SHOWN, SMOKE_ROOT } = require("./smoke-kit.cjs");

const THEMES = ["light", "dark"];
const DEMO = path.join(SMOKE_ROOT, "fixtures/plugins/derived-demo");
// ONVIF XML needs no options and gives the video one lane, which the command's lane picker offers.
const OVERLAY = path.join(SMOKE_ROOT, "fixtures/decoders/onvif_xml/frames.xml");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Whether `promise` settles within `ms`. */
const settles = (promise, ms) =>
  Promise.race([
    promise.then(
      () => true,
      () => true,
    ),
    delay(ms).then(() => false),
  ]);

/** POSTs `body` as JSON to `url` in the page, and whether the backend accepted it. */
const post = ({ js }, url, body) =>
  js(
    `fetch(${JSON.stringify(url)}, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ${JSON.stringify(JSON.stringify(body))} }).then((r) => r.ok)`,
  );

/** The frame of the page whose address is under /views/, once there is one. */
async function viewFrame(window, wait) {
  for (let i = 0; i < 500; i++) {
    const found = window.webContents.mainFrame.frames.find((frame) =>
      frame.url.includes("/views/"),
    );
    if (found) return found;
    await delay(20);
  }
  throw new Error(
    `Timed out: the view's frame\n${await wait("false").catch((error) => error.message)}`,
  );
}

/** The demo plugin's view, through everything a view must hold to (the file's head). */
async function checkView(window) {
  const { js, wait } = page(window);
  assert.ok(await post({ js }, "/api/plugins/install", { path: DEMO }), "The demo plugin installs");
  const listed = await js(
    "fetch('/api/plugins').then((r) => r.json()).then(({ plugins }) => plugins.flatMap(({ status }) => status.views ?? []).map(({ url }) => url))",
  );
  assert.equal(listed.length, 1, "The demo plugin lists its one view");
  const url = new URL(listed[0], window.webContents.getURL()).href;

  // Opened as a page of its own, the view's address has an opaque origin: the sandbox header, not the frame, does it.
  const alone = new BrowserWindow({
    show: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      session: session.fromPartition("smoke-plugin-view"),
    },
  });
  try {
    await alone.loadURL(url);
    assert.equal(await alone.webContents.executeJavaScript("window.view.origin"), "null");
  } finally {
    alone.destroy();
  }

  await js(
    "document.querySelector('[data-cursor-id=\"activity:derived-demo.derived-view\"]').click()",
  );
  const frame = await viewFrame(window, wait);
  const received = () => frame.executeJavaScript("window.view.events.map((event) => event.type)");
  for (let i = 0; i < 500 && !(await received()).includes("context"); i++) await delay(20);
  assert.ok((await received()).includes("context"), "The view gets context over its port");
  assert.equal(await frame.executeJavaScript("window.view.origin"), "null");

  // A frame may load nothing but views: a page of the app is refused, and the view keeps its page.
  await frame.executeJavaScript("location.href = '/api/plugins'").catch(() => {});
  await delay(1000);
  assert.ok(frame.url.includes("/views/"), `The view stays on its page, but it is at ${frame.url}`);

  // The frame runs in a process of its own, so a loop in it leaves the window answering.
  void frame.executeJavaScript("setTimeout(() => { for (;;); }, 0)").catch(() => {});
  await delay(500);
  assert.equal(await settles(frame.executeJavaScript("1"), 1000), false, "The view is busy");
  for (let i = 0; i < 5; i++) {
    assert.equal(
      await settles(js("1 + 1"), 2000),
      true,
      "The window answers while the view is busy",
    );
    await frames({ js });
    await delay(100);
  }
  const busy = frame.osProcessId;
  await js("document.querySelector('[data-cursor-id=\"activity:workspace\"]').click()");
  await wait("!document.querySelector('iframe')");
  // A frame in a loop is not told to stop; its process is this scenario's to end, if it is not gone already.
  if (busy !== process.pid && busy !== window.webContents.mainFrame.osProcessId) {
    try {
      process.kill(busy);
    } catch (error) {
      if (error.code !== "ESRCH") throw error;
    }
  }

  // A view that loads another page of its plugin's views is no longer the view: it loses its port and is not shown.
  await js(
    "document.querySelector('[data-cursor-id=\"activity:derived-demo.derived-view\"]').click()",
  );
  const again = await viewFrame(window, wait);
  await again.executeJavaScript("location.href = `${location.href}?again`").catch(() => {});
  await wait("document.body.innerText.includes('View left its page')");
  await wait("!document.querySelector('iframe')");
  await js("document.querySelector('[data-cursor-id=\"activity:workspace\"]').click()");
}

/**
 * The demo plugin's command dialog: its Plugins menu entry over a video with one lane opens a lane picker and the folder option,
 * and the dialog fits the smallest window at 200% zoom, in both themes. Runs in a backend of its own, which has the video.
 */
async function checkRunDialog() {
  await inBackendWindow(
    ["--video", VIDEO, "--overlay", OVERLAY, "--handler-type", "ONVIF_XML"],
    async (window) => {
      const { js, wait } = page(window);
      assert.ok(
        await post({ js }, "/api/plugins/install", { path: DEMO }),
        "The demo plugin installs",
      );
      try {
        // At 640 wide the sidebar fills the window and no viewer is in front, so the dialog opens first and is then
        // measured at the smallest size.
        await setView(window, [1100, 720], 1);
        // The command runs on a viewer that is playing: the video the command line added is open at once.
        await wait(`${SHOWN} >= 0`);
        // The Plugins menu appears once the page has read the listing after the install.
        await wait(
          "[...document.querySelectorAll('[data-top]')].some((b) => b.textContent === 'Plugins')",
        );
        await js(
          "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'Plugins').click()",
        );
        await wait("document.querySelector('[role=menu]')");
        await js(
          "[...document.querySelectorAll('[role=menuitem]')].find((m) => m.textContent.startsWith('Count samples')).click()",
        );
        await wait(
          "document.querySelector('dialog[open] select') && document.querySelector('dialog[open] input')",
        );
        await setView(window, [640, 480], 2);
        for (const theme of THEMES) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await frames({ js });
          assert.deepEqual(
            await js(`(${fit.dialog.toString()})()`),
            [],
            `The plugin command dialog must fit the 640x480 window at 200% zoom, ${theme}`,
          );
        }
        await js(
          "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').click()",
        );
        await wait("!document.querySelector('dialog')");
      } finally {
        await post({ js }, "/api/plugins/remove", { target: "derived-demo" });
      }
    },
  );
}

module.exports.run = async ({ window }) => {
  const { js } = page(window);
  try {
    await checkView(window);
  } finally {
    await post({ js }, "/api/plugins/remove", { target: "derived-demo" });
  }
  await checkRunDialog();
};
