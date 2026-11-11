const { observe, spawnOwned: spawn } = require("./child.cjs");
const { apiVersion } = require("../desktop/generated/api.json");
const STARTUP_TIMEOUT_MS = 60_000;
const PROBE_TIMEOUT_MS = 1000;
const mode = process.argv[2];
const port = process.env.PORT || "7330";
const uiPort = process.env.UI_PORT || "7331";
const origin = `http://127.0.0.1:${port}`;
const ui = `http://127.0.0.1:${uiPort}`;
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  void Promise.all(children.map((owned) => owned.stop())).then(() => process.exit(code));
}
function start(command, args, env = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    detached: process.platform !== "win32",
    env: { ...process.env, ...env },
  });
  children.push(observe(child));
  child.on("error", (error) => {
    console.error(error.message);
    stop(1);
  });
  child.on("exit", (code) => {
    if (!stopping) stop(code || 0);
  });
  return child;
}
async function available(url) {
  try {
    const response = await fetch(`${url}/api/info`, {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    const data = await response.json();
    if (!data.development)
      throw new Error(
        `A release server is already running at ${url}. Stop it or choose another PORT for development.`,
      );
    if (data.app !== "ax-devil" || data.api !== apiVersion)
      throw new Error(`Incompatible app at ${url}`);
    return true;
  } catch (error) {
    if (error.cause?.code === "ECONNREFUSED") return false;
    throw error;
  }
}
async function ready(url) {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await available(url)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`Startup timed out: ${url}`);
}
async function main() {
  if (!(await available(origin))) {
    start(
      process.env.WATCHEXEC || ".tools/bin/watchexec", // The Makefile exports the one on PATH, else .tools/.
      [
        "--restart",
        "--stop-signal",
        "SIGINT",
        "--stop-timeout",
        "2s",
        "--watch",
        "src",
        "--watch",
        "crates",
        "--watch",
        "Cargo.toml",
        "--watch",
        "Cargo.lock",
        "--watch",
        "rust-toolchain.toml",
        "--watch",
        "Makefile",
        "--",
        "scripts/dev-backend",
      ],
      { PORT: port },
    );
    await ready(origin);
  } else console.log(`Reusing backend at ${origin}`);
  if (!(await available(ui))) {
    start("pnpm", ["--dir", "frontend", "dev", "--port", uiPort], { PORT: port });
    await ready(ui);
  } else {
    const response = await fetch(`${ui}/@vite/client`);
    if (!response.ok || !(await response.text()).includes("WebSocket"))
      throw new Error(`UI_PORT is not a Vite development server: ${ui}`);
    const direct = await (await fetch(`${origin}/api/info`)).json();
    const proxied = await (await fetch(`${ui}/api/info`)).json();
    if (direct.pid !== proxied.pid)
      throw new Error("The existing frontend proxies a different backend. Choose another UI_PORT.");
    console.log(`Reusing frontend at ${ui}`);
  }
  console.log(`\nWebsite: ${ui}\n`);
  if (mode === "desktop") {
    const env = { ...process.env, PORT: port, DESKTOP_DEV_URL: ui };
    delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn("pnpm", ["--dir", "desktop", "start"], {
      stdio: "inherit",
      detached: process.platform !== "win32",
      env,
    });
    children.push(observe(child));
    child.on("error", (error) => {
      console.error(error.message);
      stop(1);
    });
    child.on("exit", (code) => stop(code || 0));
  } else setInterval(() => {}, 60000);
}
if (require.main === module) {
  process.on("SIGINT", () => stop());
  process.on("SIGTERM", () => stop());
  process.on("SIGHUP", () => stop());
  void main().catch((error) => {
    console.error(error.message);
    stop(1);
  });
}
module.exports = { STARTUP_TIMEOUT_MS, PROBE_TIMEOUT_MS };
