// The local backend: reuse a compatible one already on the port, or start the release build and own it.
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const path = require("node:path");

const { observe, BACKEND_INTERRUPT_MS, BACKEND_STOP_MS } = require("./child.cjs");

const { shellApi } = require("./generated/api.json");

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
 * The backend executable: the sidecar in the app's resources when packaged, else `AX_DEVIL_BACKEND` (the smoke test
 * runs the debug build the Rust tests already compiled), else the release build in the checkout.
 */
function backendExecutable(app) {
  const name = process.platform === "win32" ? "ax-devil.exe" : "ax-devil";
  if (app.isPackaged) return path.join(process.resourcesPath, "bin", name);
  return process.env.AX_DEVIL_BACKEND ?? path.resolve(__dirname, "../target/release", name);
}

/**
 * Starts the `executable` with `args` and resolves, once it prints its readiness line, with the child and its origin.
 * The child stops when its standard input closes, which `stop()` does and a crashed shell does too.
 * `onSpawn` gets the child the moment it exists, so the caller can stop it while it is still starting; a startup
 * that times out kills it. `onExit` runs when the process ends after the readiness line. `spawner` is a test seam.
 */
function startProcess(executable, args, env, onExit, { onSpawn = () => {}, spawner = spawn } = {}) {
  const child = spawner(executable, args, {
    env: { ...process.env, ...env, AX_DEVIL_EXIT_ON_STDIN_CLOSE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  const lifecycle = observe(child);
  // The backend may be gone by the time stdin is written or closed; that is not an error.
  child.stdin.on("error", () => {});
  child.stderr.on("data", (data) => process.stderr.write(data));
  let ready = false;
  let startReason;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("Backend startup timed out"));
      lifecycle.signal("SIGKILL");
    }, 10000);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    void lifecycle.done.then(({ code, signal, error }) => {
      clearTimeout(timer);
      if (ready) onExit(code ?? signal ?? error);
      else {
        // Give already queued stdout one turn to deliver its final reason, without awaiting inherited pipes.
        setImmediate(() => reject(new Error(startReason ?? "Backend stopped during startup")));
      }
    });
    // Exit may precede the last stdout data. Close follows both pipes, so the start reason has arrived.
    child.once("close", () => {
      clearTimeout(timer);
      if (!ready) reject(new Error(startReason ?? "Backend stopped during startup"));
    });
    createInterface({ input: child.stdout }).on("line", (line) => {
      if (line.startsWith("ax-devil-error: ")) {
        try {
          const reason = JSON.parse(line.slice("ax-devil-error: ".length));
          if (typeof reason === "string") startReason = reason;
        } catch {
          /* Only the explicit user-facing message is shown, never log output. */
        }
        return;
      }
      const match = /^ax-devil: (http:\/\/127\.0\.0\.1:\d+)/.exec(line);
      if (match && !lifecycle.ended()) {
        clearTimeout(timer);
        ready = true;
        resolve({ child, lifecycle, origin: match[1] });
      }
    });
    onSpawn(child, lifecycle);
  });
}

/**
 * The local backend, started lazily by `ensure()`. A backend found on the port is reused and left running;
 * one this module started has its stdin closed on `stop()`, which stops it gracefully; it gets SIGINT after 3 seconds
 * and SIGKILL 3 seconds later, also while it is still starting. `onUnexpectedExit` runs when an owned backend ends
 * without `stop()`. `executable` and `args` start a backend this module starts; `spawner` is a test seam.
 */
function createBackend({ port, devUrl, executable, args = [], onUnexpectedExit, spawner }) {
  let child;
  let origin;
  let lifecycle;
  let stopping = false;
  let stopped;

  async function reusable() {
    const base = `http://127.0.0.1:${port}`;
    try {
      const info = await fetchInfo(base, 1500);
      if (
        info.app !== "ax-devil" ||
        info.api !== require("./generated/api-version.json").apiVersion ||
        info.shell !== shellApi
      )
        throw new Error(`Port ${port} belongs to an incompatible server.`);
      if (!(info.owner && info.fullAccess))
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
    if (stopping) throw new Error("Backend stopped during startup");
    const started = await startProcess(
      executable,
      args,
      { PORT: port },
      (reason) => {
        child = undefined;
        origin = undefined;
        if (!stopping) onUnexpectedExit(new Error(`Backend exited unexpectedly: ${reason}`));
      },
      {
        // Recorded at once, so a stop during startup reaches the process.
        onSpawn: (spawned, observed) => {
          child = spawned;
          lifecycle = observed;
        },
        spawner,
      },
    );
    return started.origin;
  }

  return {
    /** Whether this module started the backend, which a restart of the app then restarts; false for a reused one. */
    owned: () => child !== undefined,
    /** The local backend's origin, starting or reusing it on first use. */
    ensure() {
      origin ??= start().catch((error) => {
        origin = undefined;
        throw error;
      });
      return origin;
    },
    /**
     * Stops the backend this module started, also one that is still starting; one it only reused keeps running.
     * Resolves once the process has ended and the startup, if any, has settled.
     */
    stop() {
      if (stopped) return stopped;
      stopping = true;
      const starting = origin?.catch(() => undefined);
      const owned = child;
      const ended = lifecycle?.done;
      if (owned && owned.exitCode === null && owned.signalCode === null) {
        lifecycle.after(BACKEND_INTERRUPT_MS, "SIGINT");
        lifecycle.after(BACKEND_STOP_MS, "SIGKILL");
        owned.stdin.end();
      }
      stopped = Promise.all([starting, ended]).then(() => undefined);
      return stopped;
    },
  };
}

module.exports = { backendExecutable, createBackend, fetchInfo, startProcess };
