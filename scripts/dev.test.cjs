const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const net = require("node:net");

async function freePort() {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(check) {
  for (let i = 0; i < 200; i++) {
    if (await check()) return;
    await delay(50);
  }
  throw new Error("Timed out waiting for development services");
}
async function listening(port) {
  return new Promise((resolve) => {
    const socket = net.connect(port, "127.0.0.1");
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

test(
  "supervisor starts a debug backend and stops the full service tree",
  { timeout: 20000 },
  async () => {
    const port = await freePort();
    const uiPort = await freePort();
    const child = spawn(process.execPath, ["scripts/dev.cjs", "web"], {
      env: { ...process.env, PORT: String(port), UI_PORT: String(uiPort) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr.on("data", (chunk) => {
      output += chunk;
    });
    const exited = once(child, "close");
    try {
      await waitFor(() => output.includes("Website:"));
      const response = await fetch(`http://127.0.0.1:${uiPort}/api/info`);
      assert.equal((await response.json()).development, true);
      child.kill("SIGINT");
      assert.deepEqual(await exited, [0, null], output);
      await waitFor(async () => !(await listening(port)) && !(await listening(uiPort)));
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGINT");
        await exited;
      }
    }
  },
);
