// What the command line asks of the shell and what the local backend gets from it.

/**
 * The server the command line opens (`--server URL`) and the `ax-devil://` link it carries, and the arguments left
 * over, which are not the shell's: `{ launch: { server, link }, rest }`. Throws when `--server` has no address.
 */
function parseLaunch(args) {
  const launch = {};
  const rest = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--server") {
      launch.server = args[++i];
      if (!launch.server) throw new Error("--server needs a server address.");
    } else if (arg.startsWith("--server=")) launch.server = arg.slice("--server=".length);
    else if (arg.startsWith("ax-devil://")) launch.link = arg;
    else rest.push(arg);
  }
  return { launch, rest };
}

// The backend's global options that take a value; the command after them is the first argument without a dash.
const GLOBAL_OPTIONS = new Set(["--config", "--cache-dir", "--log-level"]);

/**
 * The backend's arguments from the shell's other arguments (the `rest` of [parseLaunch], so that a server address or
 * link is not taken for the command): its global options, then the command (`local --video
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

module.exports = { backendArgs, parseLaunch, userArgs };
