// The smoke test's Live stream scenario, on a backend of its own that opens a workspace file with two saved streams:
// an MQTT one whose broker is a `$NAME` reference and whose camera head is 2, which the Live view's row opens on the
// broker as it expands; and a DataHub one whose decoder is not installed, which Edit offers as unavailable instead of a
// blank choice. Both are saved in each theme; the Edit dialog, the stream's info line and the camera row's width priority are fit-checked at the smallest
// window and at 200% zoom. The MQTT stream is then opened, on documentation addresses where no camera answers.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const fit = require("./fit.cjs");
const { frames, page, removeTemp, SCREENSHOTS, setView } = require("./smoke-kit.cjs");
const { inBackendWindow } = require("./smoke-twice.cjs");

const BROKER = "198.51.100.2";
const STREAM = {
  kind: "live",
  username: "",
  password: "",
  protocol: "https",
  rtsp_port: 554,
};
const WORKSPACE = {
  version: 2,
  name: null,
  items: [
    {
      ...STREAM,
      id: "6f1c1e3a-0000-4000-8000-000000000001",
      label: "",
      host: "198.51.100.5",
      head: 2,
      overlay: {
        mode: "mqtt",
        handler: "ADF_V1_FRAME",
        broker_host: "$AX_DEVIL_SMOKE_BROKER",
        broker_port: 1883,
        source_key: "scene",
      },
    },
    {
      ...STREAM,
      id: "6f1c1e3a-0000-4000-8000-000000000002",
      label: "Yard",
      host: "198.51.100.6",
      head: 1,
      overlay: { mode: "datahub", handler: "GONE_V1", topic: "scene", channel: 1 },
    },
  ],
  bookmarks: [],
  reviewed: [],
};

module.exports.run = async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-streams-"));
  const file = path.join(folder, "streams.ax-devil.workspace");
  await fs.writeFile(file, JSON.stringify(WORKSPACE));
  try {
    await inBackendWindow(
      [file],
      async (window) => {
        const { js, wait } = page(window);
        const row = (text, level = 1) =>
          `[...document.querySelectorAll('[role=treeitem]')].find((r) => r.getAttribute('aria-level') >= ${level} && r.textContent.includes('${text}'))`;
        const shoot = async (name) =>
          fs.writeFile(
            path.join(SCREENSHOTS, name),
            (await window.webContents.capturePage()).toPNG(),
          );
        // Each fit check runs at the normal window, the smallest window and 200% zoom in the smallest, in both themes;
        // the normal one and the 200% one are saved.
        const views = async (name, check) => {
          for (const [size, zoom, saved] of [
            [[1100, 720], 1, true],
            [[640, 480], 1, false],
            [[640, 480], 2, true],
          ]) {
            await setView(window, size, zoom);
            for (const theme of ["light", "dark"]) {
              nativeTheme.themeSource = theme;
              await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
              await frames({ js });
              const where = `${name} at ${size.join("x")}, ${zoom * 100}% zoom in the ${theme} theme`;
              assert.deepEqual(await js(check), [], `${where} must fit`);
              if (saved) await shoot(`${name}-${theme}${zoom === 1 ? "" : "-200"}.png`);
            }
          }
          await setView(window, [1100, 720], 1);
        };
        // The stream's info line is whole inside the window: measured against its own box, whatever the font.
        const infoFits = `() => {
          const line = [...document.querySelectorAll('[role=treeitem] *')].find(
            (e) => e.children.length === 0 && e.textContent === '${BROKER}:1883',
          );
          if (!line) return ['the info line is not there'];
          const box = line.getBoundingClientRect();
          const problems = [];
          if (line.scrollWidth > line.clientWidth + 1) problems.push('the info line is cut off');
          if (box.left < 0 || box.right > innerWidth + 1) problems.push('the info line is outside the window');
          return problems;
        }`;
        // A camera row keeps room for six characters of its name, and its aside gives way before the name is cut (the
        // list row rule). Checked with the info line, at the same sizes.
        const nameKeepsRoom = `() => {
          const problems = [];
          for (const item of document.querySelectorAll('[role=treeitem][aria-level="1"]')) {
            const [name, aside] = [...item.firstElementChild.children].filter((e) =>
              e.classList.contains('truncate'),
            );
            if (!name || !aside) continue;
            const probe = document.createElement('span');
            probe.style.cssText = 'position:absolute;visibility:hidden;width:6ch';
            probe.style.font = getComputedStyle(name).font;
            document.body.append(probe);
            const least = probe.getBoundingClientRect().width;
            probe.remove();
            if (name.clientWidth + 1 < least) problems.push('the name has less than six characters');
            if (name.scrollWidth > name.clientWidth + 1 && aside.clientWidth > 1)
              problems.push('the name is cut while the aside still shows');
          }
          return problems;
        }`;
        await fs.mkdir(SCREENSHOTS, { recursive: true });
        await setView(window, [1100, 720], 1);
        await js("document.querySelector('nav [aria-label=Live][aria-pressed=false]')?.click()");
        await wait("document.querySelector('nav [aria-label=Live][aria-pressed=true]')");

        // The stream opens on the broker as the reference expands, and on the host with its head otherwise. The page
        // as a whole is not fit-checked: the failed stream beside it ends its reason in an ellipsis, which that check
        // reads as cut off.
        await wait(row("scene"));
        await js(`${row("scene")}.click()`);
        await wait(row(`${BROKER}:1883`));
        await views("live-stream-info", `[...(${infoFits})(), ...(${nameKeepsRoom})()]`);

        // A saved decoder the app no longer has is offered as unavailable in the Edit dialog.
        await js(
          `${row("Yard", 2)}.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }))`,
        );
        await wait("document.querySelector('[role=menu]')");
        await js(
          "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Edit')).click()",
        );
        await wait("document.querySelector('dialog[open] h2')?.textContent === 'Edit Live Stream'");
        await wait(
          `[...document.querySelectorAll('dialog[open] option')].some((o) => o.textContent === 'GONE_V1 (unavailable)')`,
        );
        await views("live-edit-unavailable", `(${fit.dialog.toString()})()`);
        await js(
          "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').click()",
        );
        await wait("!document.querySelector('dialog')");

        // Opening a stream shows what it waits for until its first picture; the address answers nothing.
        await js(
          `${row("scene")}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }))`,
        );
        await wait(
          "[...document.querySelectorAll('[role=toolbar][aria-label=Live] ~ div p')].some((p) => p.textContent.startsWith('Connecting') || p.textContent.startsWith('Reconnecting'))",
        );
        for (const theme of ["light", "dark"]) {
          nativeTheme.themeSource = theme;
          await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
          await frames({ js });
          await shoot(`live-viewer-waiting-${theme}.png`);
        }
      },
      { command: "open", env: { AX_DEVIL_SMOKE_BROKER: BROKER } },
    );
  } finally {
    await removeTemp(folder);
  }
};
