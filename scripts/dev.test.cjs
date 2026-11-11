const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const events = require("node:events");
const net = require("node:net");
const { STARTUP_TIMEOUT_MS, PROBE_TIMEOUT_MS } = require("./dev.cjs");
// Each service has an initial port probe and may finish its last readiness probe after its deadline.
const START_LIMIT_MS = 2 * (STARTUP_TIMEOUT_MS + 2 * PROBE_TIMEOUT_MS);

async function freePorts() {
  const servers = [net.createServer(), net.createServer()];
  try {
    // Keep both reservations alive until the kernel has allocated distinct ports.
    await Promise.all(
      servers.map(async (server) => {
        server.listen(0, "127.0.0.1");
        await events.once(server, "listening");
      }),
    );
    return servers.map((server) => server.address().port);
  } finally {
    await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))));
  }
}
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(check, limitMs = 10000) {
  const deadline = Date.now() + limitMs;
  while (Date.now() < deadline) {
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

void test(
  "supervisor starts a debug backend and stops the full service tree",
  { timeout: START_LIMIT_MS + 20000 },
  async () => {
    const [port, uiPort] = await freePorts();
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
    const exited = events.once(child, "exit", {
      signal: AbortSignal.timeout(START_LIMIT_MS + 10000),
    });
    // Observe spawn failure immediately even while the startup condition is still being checked.
    void exited.catch(() => undefined);
    try {
      await waitFor(() => {
        assert.ok(
          child.exitCode === null && child.signalCode === null,
          `Supervisor exited before readiness:\n${output}`,
        );
        return output.includes("Website:");
      }, START_LIMIT_MS);
      assert.doesNotMatch(output, /Reusing (backend|frontend)/, "the run must own both services");
      const info = await Promise.all(
        [port, uiPort].map(async (servicePort) => {
          const response = await fetch(`http://127.0.0.1:${servicePort}/api/info`, {
            signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
          });
          assert.equal(response.ok, true);
          return response.json();
        }),
      );
      assert.equal(info[0].development, true);
      assert.equal(info[1].pid, info[0].pid, "frontend must proxy this run's backend");
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
