// The smoke test's Plugins view: only the built-in plugins to begin with, then one installed through the view (with a handler it skips) and one
// that stopped loading because its manifest was damaged after it was installed, in both themes at the smallest window
// and 200% zoom. Updating the first after editing its manifest shows the new version. Removing both leaves the registry as it was.
// Then the demo plugin's view, installed: it mounts and gets `context` over its port, its address opened as a page has the
// opaque origin, a frame cannot load anything but views, a view that loads another page loses its port, and a view in a
// busy loop leaves the window responsive because its frame runs in a process of its own. Its command's dialog, opened by
// the Plugins menu over a video with a lane, fits the smallest window at 200% zoom in both themes.
const { BrowserWindow, nativeTheme, session } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const fit = require("./fit.cjs");
const { inBackendWindow, VIDEO } = require("./smoke-twice.cjs");
const {
  button,
  frames,
  page,
  removeTemp,
  SCREENSHOTS,
  setView,
  SHOWN,
  showChrome,
  SMOKE_ROOT,
} = require("./smoke-kit.cjs");

const THEMES = ["light", "dark"];
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

/** The demo plugin's view, installed with `install`, through everything a view must hold to (the file's head). */
async function checkView(window, install, demo) {
  const { js, wait } = page(window);
  assert.ok(await install(demo), "The demo plugin installs");
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

  // A frame may load nothing but views: a page of the app is refused, and the view keeps its page and its port.
  await frame.executeJavaScript("location.href = '/api/plugins'").catch(() => {});
  await delay(1000);
  assert.ok(frame.url.includes("/views/"), `The view stays on its page, but it is at ${frame.url}`);
  assert.ok(
    await js("!document.body.innerText.includes('View left its page')"),
    "A refused load is no load",
  );

  // The frame runs in a process of its own and a loop in it leaves the window answering.
  assert.notEqual(
    frame.osProcessId,
    window.webContents.mainFrame.osProcessId,
    "The view's frame must not share the page's process",
  );
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
  // A frame in a loop is not told to stop; its process is this scenario's to end.
  // The process may be gone already once its frame was removed.
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
async function checkRunDialog(demo) {
  await inBackendWindow(
    ["--video", VIDEO, "--overlay", OVERLAY, "--handler-type", "ONVIF_XML"],
    async (window) => {
      const { js, wait } = page(window);
      const post = (url, body) =>
        js(
          `fetch(${JSON.stringify(url)}, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ${JSON.stringify(JSON.stringify(body))} }).then((r) => r.ok)`,
        );
      assert.ok(await post("/api/plugins/install", { path: demo }), "The demo plugin installs");
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
        await post("/api/plugins/remove", { target: "derived-demo" });
      }
    },
  );
}

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const eachTheme = async (visit) => {
    for (const theme of THEMES) {
      nativeTheme.themeSource = theme;
      await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames({ js });
      await visit(theme);
    }
  };
  const fits = async (what) => {
    await showChrome(window);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Plugins view must fit: ${what}`,
    );
  };
  // A list that grows with the user's plugins scrolls, so only its width and the install form are held to the window.
  const keepsWidth = async (what) =>
    assert.ok(
      await js(`(() => {
        const aside = document.querySelector('aside');
        const install = [...aside.querySelectorAll('button')].find((b) => b.textContent === 'Install').getBoundingClientRect();
        return aside.scrollWidth <= aside.clientWidth && install.bottom <= innerHeight && install.right <= innerWidth;
      })()`),
      `The Plugins view must not overflow sideways or push the install form out: ${what}`,
    );

  // Opens the Plugins view, which a second click on its button would close.
  const show = async () => {
    await js("document.querySelector('[aria-label^=Plugins][aria-pressed=false]')?.click()");
    await wait("document.querySelector('aside section[aria-label=\"Built-in\"]')");
  };

  const source = path.join(SMOKE_ROOT, "fixtures/plugins/shell-times");
  const demoSource = path.join(SMOKE_ROOT, "fixtures/plugins/derived-demo");
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-plugins-"));
  try {
    const good = path.join(folder, "good");
    const broken = path.join(folder, "broken");
    await fs.cp(source, good, { recursive: true });
    await fs.cp(source, broken, { recursive: true });
    // A handler of a kind this build does not know is listed as skipped.
    const goodManifest = path.join(good, "ax-devil-plugin.json");
    const goodJson = JSON.parse(await fs.readFile(goodManifest, "utf8"));
    goodJson.handlers.push({ kind: "encoder", id: "shell-times-encode" });
    await fs.writeFile(goodManifest, JSON.stringify(goodJson, null, 2));
    const manifest = path.join(broken, "ax-devil-plugin.json");
    await fs.writeFile(
      manifest,
      (await fs.readFile(manifest, "utf8")).replaceAll("shell-times", "broken-times"),
    );

    await setView(window, [640, 480], 2);
    await show();
    await eachTheme(async (theme) => {
      await fits(`empty, ${theme}, 640x480 at 200%`);
      await shoot(`plugins-empty-${theme}-200.png`);
    });

    // The person types a folder, which the backend installs and lists at once.
    await js("document.querySelector('aside input').focus()");
    window.webContents.insertText(good);
    await js(`${button("Install")}.click()`);
    await wait("document.querySelector('aside section[aria-label=Installed]')");

    // A second plugin whose manifest breaks afterwards stays listed as failed, with the reason.
    const install = (target) =>
      js(
        `fetch('/api/plugins/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: ${JSON.stringify(target)} }) }).then((r) => r.ok)`,
      );
    assert.ok(await install(broken), "The second plugin installs");
    await fs.writeFile(manifest, "{ not json");
    // The running app notices the edited manifest on its own; the view looks again when it is shown.
    for (let i = 0; i < 50; i++) {
      await js("document.querySelector('[aria-label=Workspace][aria-pressed=false]')?.click()");
      await show();
      if (await js("!!document.querySelector('aside section[aria-label=Failed]')")) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    await wait("document.querySelector('aside section[aria-label=Failed] [role=alert]')");
    // The failed plugin flags Plugins on the activity bar, where the tooltip says how many.
    await wait(
      "document.querySelector('nav[aria-label=Activity] [aria-label=\"Plugins: 1 failed to load\"]')",
    );
    await eachTheme(async (theme) => {
      await keepsWidth(`${theme}, 640x480 at 200%`);
      await shoot(`plugins-${theme}-200.png`);
    });

    await setView(window, [1100, 720], 1);
    await eachTheme(async (theme) => {
      await fits(`${theme}, 1100x720`);
      await shoot(`plugins-${theme}.png`);
    });
    // Update reads an edited manifest again.
    goodJson.version = "1.1.0";
    await fs.writeFile(goodManifest, JSON.stringify(goodJson, null, 2));
    await js(`document.querySelector('aside button[aria-label="Update Shell times"]').click()`);
    await wait(
      "[...document.querySelectorAll('aside section[aria-label=Installed] li')].some((li) => li.textContent.includes('1.1.0'))",
    );
    for (const name of ["Remove broken", "Remove Shell times"]) {
      await js(`document.querySelector('aside button[aria-label="${name}"]').click()`);
      await wait(`!document.querySelector('aside button[aria-label="${name}"]')`);
    }
    assert.ok(
      await js("!document.querySelector('aside section[aria-label=Installed]')"),
      "Removing both plugins leaves only the built-ins",
    );
    await wait("!document.querySelector('nav[aria-label=Activity] [aria-label^=\"Plugins: \"]')");

    const demo = path.join(folder, "derived-demo");
    await fs.cp(demoSource, demo, { recursive: true });
    await checkView(window, install, demo);
    await show();
    await js(`document.querySelector('aside button[aria-label="Remove Derived demo"]').click()`);
    await wait(`!document.querySelector('aside button[aria-label="Remove Derived demo"]')`);
    await checkRunDialog(demo);
  } finally {
    await removeTemp(folder);
  }
  await js("document.querySelector('[aria-label=Workspace][aria-pressed=false]')?.click()");
};
