// What the command line asks of the shell and what the local backend gets from it.

/**
 * The server the command line opens (`--server URL`) and the `ax-devil://` link it carries, and the arguments left
 * over, which are not the shell's: `{ launch: { server, link }, rest }`. A link wins over `--server`. Throws when `--server`
 * has no address.
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
  // A link launch is the system's, which a stray `--server` among its arguments must not redirect.
  if (launch.link) delete launch.server;
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

/**
 * The arguments a restarted shell starts with: the app (`.` in development), the backend's global options and the
 * shell's own and Chromium's switches. The server address, link and the command with its arguments are dropped,
 * because a restart opens the local backend without content whichever window asked.
 */
function relaunchArgs(argv, defaultApp) {
  const app = defaultApp ? argv.slice(1, 2) : [];
  const args = parseLaunch(userArgs(argv, defaultApp)).rest;
  const kept = [];
  let commandSeen = false;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (!arg.startsWith("-")) {
      commandSeen = true;
      continue;
    }
    const global = GLOBAL_OPTIONS.has(arg.split("=")[0]);
    // Past the command, only the backend's global options are kept; the rest are the command's.
    if (commandSeen && !global) continue;
    kept.push(arg);
    if (global && !arg.includes("=") && i + 1 < args.length) kept.push(args[++i]);
  }
  return [...app, ...kept];
}

module.exports = { backendArgs, parseLaunch, relaunchArgs, userArgs };
