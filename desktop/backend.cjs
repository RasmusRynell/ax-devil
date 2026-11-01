// The local backend: reuse a compatible one already on the port, or start the release build and own it.
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const path = require("node:path");

const { apiVersion, shellApi } = require("./generated/api.json");

// What the shell needs from a backend it opens without a password: every grant a local window uses.
const LOCAL_GRANTS = ["preferences", "configuration", "sessions"];

/** Reads a server's `/api/info`; rejects when it cannot be reached or does not answer with JSON. */
async function fetchInfo(base, timeoutMs) {
  const response = await fetch(`${base}/api/info`, {
    signal: AbortSignal.timeout(timeoutMs),
    // An answer from another origin says nothing about the one that will be loaded.
    redirect: "error",
  });
  if (!response.ok) throw new Error(`${base} answered ${response.status}`);
  return response.json();
}

/**
 * Starts an ax-devil process and resolves, once it prints its readiness line, with the child and its origin.
 * `onExit` runs when the process ends after that.
 */
function startProcess(args, env, onExit) {
  const executable = path.resolve(__dirname, "../target/release/ax-devil");
  const child = spawn(executable, args, {
    env: { ...process.env, ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stderr.on("data", (data) => process.stderr.write(data));
  let ready = false;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Backend startup timed out")), 10000);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timer);
      if (ready) onExit(code ?? signal);
      else reject(new Error("Backend stopped during startup"));
    });
    createInterface({ input: child.stdout }).on("line", (line) => {
      const match = /^ax-devil: (http:\/\/127\.0\.0\.1:\d+)/.exec(line);
      if (match) {
        clearTimeout(timer);
        ready = true;
        resolve({ child, origin: match[1] });
      }
    });
  });
}

/**
 * The local backend, started lazily by `ensure()`. A backend found on the port is reused and left running;
 * one this module started gets SIGINT on `stop()`, then SIGKILL after 3 seconds.
 * `onUnexpectedExit` runs when an owned backend ends without `stop()`. `args` are the command-line arguments for
 * a backend this module starts.
 */
function createBackend({ port, devUrl, args = [], onUnexpectedExit }) {
  let child;
  let origin;
  let stopping = false;

  async function reusable() {
    const base = `http://127.0.0.1:${port}`;
    try {
      const info = await fetchInfo(base, 1500);
      if (info.app !== "ax-devil" || info.api !== apiVersion || info.shell !== shellApi)
        throw new Error(`Port ${port} belongs to an incompatible server.`);
      if (!LOCAL_GRANTS.every((grant) => info.grants?.includes(grant)))
        throw new Error(`Port ${port} does not give this app full access.`);
      // A development backend serves no frontend; only the development supervisor pairs it with one.
      if (info.development && !devUrl)
        throw new Error(
          `Port ${port} runs a development backend; use make desktop or another PORT.`,
        );
      return base;
    } catch (error) {
      if (error.cause?.code === "ECONNREFUSED") return undefined;
      throw error;
    }
  }

  async function start() {
    const reused = port === "0" ? undefined : await reusable();
    if (reused) {
      if (args.length)
        console.error(`Ignoring the command line: the backend on port ${port} is already running.`);
      return reused;
    }
    const started = await startProcess(args, { PORT: port }, (reason) => {
      child = undefined;
      origin = undefined;
      if (!stopping) onUnexpectedExit(new Error(`Backend exited unexpectedly: ${reason}`));
    });
    child = started.child;
    return started.origin;
  }

  return {
    /** The local backend's origin, starting or reusing it on first use. */
    ensure() {
      origin ??= start().catch((error) => {
        origin = undefined;
        throw error;
      });
      return origin;
    },
    /** Stops the backend this module started; one it only reused keeps running. */
    stop() {
      stopping = true;
      const owned = child;
      if (!owned || owned.exitCode !== null || owned.signalCode !== null) return Promise.resolve();
      return new Promise((resolve) => {
        const timer = setTimeout(() => owned.kill("SIGKILL"), 3000);
        owned.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
        owned.kill("SIGINT");
      });
    },
  };
}

module.exports = { createBackend, fetchInfo, startProcess };
