// Which of Electron's own command-line arguments the backend should get.

// The backend's global options that take a value; the command after them is the first argument without a dash.
const GLOBAL_OPTIONS = new Set(["--config", "--cache-dir", "--log-level"]);

/**
 * The backend's arguments from the ones after the app itself: its global options, then the command (`local --video
 * PATH`) and everything after it. Dashed arguments before the command that are not the backend's, such as
 * `--ozone-platform=x11`, are Chromium's or the shell's own and stay out; put them before the command.
 */
function backendArgs(args) {
  const forwarded = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (!arg.startsWith("-")) return [...forwarded, ...args.slice(i)];
    if (!GLOBAL_OPTIONS.has(arg.split("=")[0])) continue;
    forwarded.push(arg);
    if (!arg.includes("=") && i + 1 < args.length) forwarded.push(args[++i]);
  }
  return forwarded;
}

/** The arguments after the app: `electron . ARGS` in development, `ax-devil ARGS` when packaged. */
function userArgs(argv, defaultApp) {
  return argv.slice(defaultApp ? 2 : 1);
}

module.exports = { backendArgs, userArgs };
