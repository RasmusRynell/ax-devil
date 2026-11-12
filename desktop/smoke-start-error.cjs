const { app } = require("electron");
const { spawn, execFile } = require("node:child_process");
const { promisify } = require("node:util");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");
const { observe } = require("./child.cjs");
const { until, SCREENSHOTS } = require("./smoke-kit.cjs");
const { input } = require("./smoke-x11.cjs");
const execute = promisify(execFile);

module.exports.run = async ({ window }) => {
  window.hide();
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-start-error-"));
  const config = path.join(folder, "configuration-with-a-long-name-for-startup-recovery.json");
  try {
    await fs.writeFile(
      config,
      JSON.stringify({
        version: 1,
        settings: {
          "storage.cache_dir": `relative/${"long-folder-name-".repeat(12)}`,
          "camera.password": "MUST-NOT-REACH-DIALOG",
        },
      }),
    );
    for (const theme of ["light", "dark"]) {
      const result = path.join(folder, `result-${theme}`);
      const child = spawn(
        process.execPath,
        [app.getAppPath(), "--ozone-platform=x11", "--smoke-test", "--config", config],
        {
          env: {
            ...process.env,
            XDG_CACHE_HOME: path.join(folder, theme, "cache"),
            XDG_CONFIG_HOME: path.join(folder, theme, "config"),
            AX_DEVIL_SMOKE_START_ERROR: "1",
            AX_DEVIL_SMOKE_RESULT: result,
            GTK_THEME: theme === "dark" ? "Adwaita:dark" : "Adwaita",
          },
          stdio: ["ignore", "ignore", "pipe"],
        },
      );
      const lifecycle = observe(child);
      let errors = "";
      child.stderr.on("data", (data) => {
        errors += data;
      });
      try {
        await until(async () => {
          try {
            return (await fs.readFile(result, "utf8")).includes("Invalid storage directory");
          } catch {
            return false;
          }
        }, "the child shell to receive the backend startup reason");
        await until(
          async () => (await input("error-dialog")).trim() === "ready",
          "native startup error dialog without a main window",
        );
        await execute("import", [
          "-window",
          "root",
          path.join(SCREENSHOTS, `start-error-${theme}.png`),
        ]);
        assert.ok(
          !errors.includes("MUST-NOT-REACH-DIALOG"),
          "startup diagnostics do not expose credentials",
        );
        await input("enter");
        let timer;
        const exited = await Promise.race([
          lifecycle.done,
          new Promise(
            (_resolve, reject) =>
              (timer = setTimeout(
                () => reject(new Error("Startup error did not quit through shutdown")),
                10000,
              )),
          ),
        ]);
        clearTimeout(timer);
        assert.equal(
          exited.code,
          1,
          "closing the error exits with failure through existing shutdown",
        );
        assert.equal(exited.signal, null);
      } finally {
        await lifecycle.stop();
      }
    }
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
    window.show();
    window.focus();
  }
};
