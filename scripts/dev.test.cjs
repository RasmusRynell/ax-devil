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

void test(
  "terminal SIGHUP and SIGTERM stop the fake owned service and preserve the reused frontend",
  { timeout: 10000, skip: process.platform === "win32" },
  async () => {
    const fs = require("node:fs");
    const os = require("node:os");
    const path = require("node:path");
    const http = require("node:http");
    const { apiVersion } = require("../desktop/generated/api.json");
    for (const signal of ["SIGHUP", "SIGTERM"]) {
      const folder = fs.mkdtempSync(path.join(os.tmpdir(), "dev-signal-"));
      const [port, uiPort] = await freePorts();
      const pidFile = path.join(folder, "pid");
      const stoppedFile = path.join(folder, "stopped");
      const service = path.join(folder, "service");
      fs.writeFileSync(
        service,
        `#!${process.execPath}
      const fs = require('node:fs');
      const server = require('node:http').createServer((_, res) => res.end(JSON.stringify({ app: 'ax-devil', api: ${JSON.stringify(apiVersion)}, development: true, pid: process.pid })));
      server.listen(process.env.PORT, '127.0.0.1', () => fs.writeFileSync(${JSON.stringify(pidFile)}, String(process.pid)));
      process.on('SIGINT', () => { fs.writeFileSync(${JSON.stringify(stoppedFile)}, 'SIGINT'); server.close(() => process.exit(0)); });
    `,
        { mode: 0o755 },
      );
      const frontend = http.createServer((req, res) => {
        if (req.url === "/@vite/client") res.end("WebSocket");
        else
          res.end(
            JSON.stringify({
              app: "ax-devil",
              api: apiVersion,
              development: true,
              pid: Number(fs.readFileSync(pidFile, "utf8")),
            }),
          );
      });
      frontend.listen(uiPort, "127.0.0.1");
      await events.once(frontend, "listening");
      const child = spawn(process.execPath, ["scripts/dev.cjs", "web"], {
        env: { ...process.env, PORT: String(port), UI_PORT: String(uiPort), WATCHEXEC: service },
        stdio: ["ignore", "pipe", "pipe"],
      });
      const exited = events.once(child, "exit", { signal: AbortSignal.timeout(5000) });
      void exited.catch(() => undefined);
      let output = "";
      child.stdout.on("data", (chunk) => {
        output = `${output}${chunk}`;
      });
      child.stderr.on("data", (chunk) => {
        output = `${output}${chunk}`;
      });
      try {
        await waitFor(() => {
          assert.equal(child.exitCode, null, output);
          return output.includes("Website:");
        }, 4000);
        child.kill(signal);
        assert.deepEqual(await exited, [0, null], output);
        assert.equal(fs.readFileSync(stoppedFile, "utf8"), "SIGINT");
        assert.equal(await listening(port), false);
        assert.equal(await listening(uiPort), true);
      } finally {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill("SIGTERM");
          await exited;
        }
        await new Promise((resolve) => frontend.close(resolve));
        fs.rmSync(folder, { recursive: true, force: true });
      }
    }
  },
);
