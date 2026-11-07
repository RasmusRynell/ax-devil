// A second `ax-devil local --video ...` while the app runs: the backend executable builds what the command asks for
// (in the second launch's working folder, so its relative paths mean what the user typed) and the running backend
// queues it for the window the shell opens next.
const { execFile } = require("node:child_process");

/** The reason the backend gave for refusing the launch: its JSON error's `reason`, else the text. */
function reasonOf(text) {
  try {
    return JSON.parse(text).reason ?? text;
  } catch {
    return text;
  }
}

/** Runs `executable --hand-off ...args` in `cwd`; resolves with what it printed, rejects with the reason it printed. */
function build(executable, args, cwd) {
  return new Promise((resolve, reject) => {
    execFile(
      executable,
      ["--hand-off", ...args],
      { cwd, timeout: 15000, windowsHide: true },
      (error, stdout) => {
        if (error) reject(new Error(stdout.trim() || error.message));
        else resolve(stdout);
      },
    );
  });
}

/**
 * Hands the command `args` (the backend's arguments, with a command) of a launch in `cwd` to the backend at `origin`.
 * Rejects with the reason when either refuses it. `run` is a test seam for starting the executable.
 */
async function handOff(executable, origin, args, cwd, run = build) {
  const handed = await run(executable, args, cwd);
  const response = await fetch(`${origin}/api/launch`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: handed,
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(reasonOf(await response.text()));
}

module.exports = { handOff };
