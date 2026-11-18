const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

/** The backend's isolated home, never Electron's OS-account home, with native path operations. */
function isolatedHome(
  env = process.env,
  paths = path,
  realpath = fs.realpathSync.native,
  temporary = os.tmpdir(),
) {
  const home = paths.sep === "\\" ? env.USERPROFILE : env.HOME;
  if (!home || !paths.isAbsolute(home))
    throw new Error("Smoke fixtures require an absolute isolated home");
  const inside = paths.relative(realpath(temporary), realpath(home));
  if (!inside || inside.startsWith("..") || paths.isAbsolute(inside))
    throw new Error("Smoke fixtures require a home inside the temporary folder");
  // Keep the backend's spelling: macOS realpath may add /private and Windows may use mixed separators.
  return home;
}

module.exports = { isolatedHome };
