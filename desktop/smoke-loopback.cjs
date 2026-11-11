// Real decoding, recovery and hover pictures over the camera tests' loopback RTSP peer.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { createInterface } = require("node:readline");
const { page, frames, settled, SCREENSHOTS, SMOKE_ROOT } = require("./smoke-kit.cjs");
const { inBackendWindow } = require("./smoke-twice.cjs");

module.exports.run = async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-loopback-"));
  const child = spawn(
    path.join(
      SMOKE_ROOT,
      `target/debug/examples/loopback_rtsp${process.platform === "win32" ? ".exe" : ""}`,
    ),
    [],
    {
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  // Observe lifecycle before the first await, including a spawn error or exit before readiness.
  let ended = false;
  let rejectPorts;
  const lines = createInterface({ input: child.stdout });
  const ports = new Promise((resolve, reject) => {
    rejectPorts = reject;
    lines.once("line", resolve);
  });
  const completed = new Promise((resolve) => {
    child.once("error", (error) => {
      ended = true;
      rejectPorts(error);
      resolve();
    });
    child.once("exit", (code, signal) => {
      ended = true;
      rejectPorts(new Error(`Loopback fixture exited before readiness: ${code ?? signal}`));
      resolve();
    });
  });
  const deadline = setTimeout(
    () => rejectPorts(new Error("Loopback fixture did not become ready")),
    10000,
  );
  const theme = nativeTheme.themeSource;
  try {
    const line = await ports;
    clearTimeout(deadline);
    const [rtsp, http] = line.split(" ").map(Number);
    const control = async (command) => {
      const response = await fetch(`http://127.0.0.1:${http}/${command}`, {
        signal: AbortSignal.timeout(5000),
      });
      assert.ok(response.ok);
      return response.text();
    };
    const file = path.join(folder, "live.ax-devil.workspace");
    await fs.writeFile(
      file,
      JSON.stringify({
        version: 2,
        name: null,
        bookmarks: [],
        reviewed: [],
        items: [
          {
            kind: "live",
            id: "6f1c1e3a-0000-4000-8000-000000000001",
            label: "Loopback",
            host: "127.0.0.1",
            username: "",
            password: "",
            protocol: "http",
            port: http,
            rtsp_port: rtsp,
            head: 1,
            overlay: { mode: "none" },
          },
        ],
      }),
    );
    await inBackendWindow(
      [file],
      async (window) => {
        const { js, wait } = page(window);
        const row = `[...document.querySelectorAll('[role=treeitem]')].filter(r => r.textContent.includes('Loopback')).at(-1)`;
        const toolbar = `document.querySelector('[role=toolbar][aria-label=Live]')`;
        const hover = async () => {
          const point = await settled(
            window,
            `(() => { const r = (${row}).getBoundingClientRect(); return {x: Math.round(r.x + 30), y: Math.round(r.y + r.height / 2)}; })()`,
          );
          window.webContents.sendInputEvent({ type: "mouseMove", ...point });
        };
        const shoot = async (state) => {
          for (const shown of ["light", "dark"]) {
            nativeTheme.themeSource = shown;
            await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
            await frames({ js });
            await fs.writeFile(
              path.join(SCREENSHOTS, `loopback-${state}-${shown}.png`),
              (await window.webContents.capturePage()).toPNG(),
            );
          }
        };
        // Read rendered pixels, not styles: each source picture has a distinct colour.
        const colour = async (channel, hover = false) => {
          const box = await js(
            `(() => { const c = document.querySelector(${JSON.stringify(hover ? "[aria-hidden] canvas" : "canvas:not([aria-hidden] canvas)")}); if (!c) return null; const r = c.getBoundingClientRect(); return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), width: 1, height: 1 }; })()`,
          );
          if (!box) return false;
          const pixel = (await window.webContents.capturePage(box)).toBitmap();
          const index = { red: 2, green: 1, blue: 0 }[channel];
          return pixel[index] > 80 && pixel[index] > pixel[(index + 1) % 3] * 2;
        };
        const picture = async (channel, hover = false) => {
          for (let attempt = 0; attempt < 300; attempt++) {
            await frames({ js });
            if (await colour(channel, hover)) return;
          }
          throw new Error(`No ${channel} picture rendered`);
        };
        await fs.mkdir(SCREENSHOTS, { recursive: true });
        await wait(row);
        await js(`${row}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))`);
        await wait(`${toolbar}?.textContent.includes('Connecting')`);
        await hover();
        await shoot("connecting");
        assert.equal(
          await js(`!!document.querySelector('[aria-hidden] canvas')`),
          false,
          "no snapshot while connecting",
        );
        await control("play");
        await wait(`${toolbar}?.textContent.includes('Live')`);
        await picture("red");
        await hover();
        await picture("red", true);
        await shoot("open");
        assert.equal(
          await control("snapshots"),
          "0",
          "connecting and open previews never ask for a snapshot",
        );
        await control("drop");
        await wait(`${toolbar}?.textContent.includes('Reconnecting')`);
        assert.ok(await colour("red"), "recovery retains the last picture");
        assert.equal(
          await js(`${toolbar}.textContent.includes('Retry')`),
          false,
          "automatic recovery needs no manual retry",
        );
        await shoot("recovering");
        await control("play");
        await picture("green");
        await picture("green", true);
        await shoot("recovered");
        await control("codec");
        await wait(`${toolbar}?.textContent.includes('Unsupported')`);
        await wait(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry')`);
        assert.ok(await colour("green"), "terminal error retains the last picture");
        await shoot("terminal");
        await js(`document.querySelector('button[aria-label="Close this pane"]').click()`);
        await wait(`!${toolbar}`);
        window.webContents.sendInputEvent({ type: "mouseMove", x: 1000, y: 500 });
        await frames({ js });
        await hover();
        await picture("blue", true);
        await shoot("closed");
        assert.equal(await control("snapshots"), "1", "closed previews share one snapshot read");
      },
      { command: "open" },
    );
  } finally {
    nativeTheme.themeSource = theme;
    clearTimeout(deadline);
    lines.close();
    try {
      if (!ended) child.kill();
      await completed;
    } finally {
      await fs.rm(folder, { recursive: true, force: true });
    }
  }
};
