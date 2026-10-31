const { spawn } = require("node:child_process");
const { apiVersion } = require("../desktop/generated/api.json");
const mode = process.argv[2];
const port = process.env.PORT || "7330";
const uiPort = process.env.UI_PORT || "7331";
const origin = `http://127.0.0.1:${port}`;
const ui = `http://127.0.0.1:${uiPort}`;
const children = [];
let stopping = false;
function signalTree(child, signal) {
  if (!child.pid) return;
  try {
    if (process.platform === "win32") child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) signalTree(child, "SIGINT");
  const timeout = setTimeout(() => {
    for (const child of children) signalTree(child, "SIGKILL");
  }, 3000);
  void Promise.all(
    children.map((child) =>
      child.exitCode !== null || child.signalCode !== null
        ? Promise.resolve()
        : new Promise((r) => child.once("exit", r)),
    ),
  ).then(() => {
    clearTimeout(timeout);
    process.exit(code);
  });
}
function start(command, args, env = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    detached: process.platform !== "win32",
    env: { ...process.env, ...env },
  });
  children.push(child);
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
    const response = await fetch(`${url}/api/info`, { signal: AbortSignal.timeout(1000) });
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
  for (let i = 0; i < 600; i++) {
    if (await available(url)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`Startup timed out: ${url}`);
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
(async () => {
  if (!(await available(origin))) {
    start(
      ".tools/bin/watchexec",
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
    children.push(child);
    child.on("error", (error) => {
      console.error(error.message);
      stop(1);
    });
    child.on("exit", (code) => stop(code || 0));
  } else setInterval(() => {}, 60000);
})().catch((error) => {
  console.error(error.message);
  stop(1);
});
