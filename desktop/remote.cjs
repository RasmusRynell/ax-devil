// Another machine's server: what to load, whether this shell may load it, and what to ask when that fails.
const { dialog } = require("electron");

const { fetchInfo } = require("./backend.cjs");
const { shellApi } = require("./generated/api.json");

/**
 * Splits a server address or join link into `base` (where the server's routes live), `origin` (the only origin its
 * window trusts) and `url` (what to load). The password rides in the fragment, as a copied link carries it.
 */
function parseTarget(text, password) {
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new Error(`"${text}" is not a web address.`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:")
    throw new Error(`"${text}" is not an http or https address.`);
  const base = `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
  const fragment = url.hash || (password ? `#password=${encodeURIComponent(password)}` : "");
  return { base, origin: url.origin, url: `${base}/${fragment}` };
}

/** Rejects, saying why, unless `base` is an ax-devil server whose frontend needs the shell interface this app has. */
async function verifyServer(base) {
  let info;
  try {
    info = await fetchInfo(base, 5000);
  } catch (error) {
    throw new Error(
      `Cannot reach ${base}: ${error.cause?.code ?? error.cause?.message ?? error.message}`,
    );
  }
  if (info.app !== "ax-devil") throw new Error(`${base} is not an ax-devil server.`);
  if (info.shell !== shellApi)
    throw new Error(
      `The server at ${base} (ax-devil ${info.version ?? "unknown"}) needs desktop shell version ` +
        `${info.shell ?? "unknown"}; this app is version ${shellApi}.`,
    );
}

/** Asks what to do about a failure: `"retry"`, `"local"` (back to the local backend) or `"quit"`. */
async function askAfterFailure(parent, reason) {
  const options = {
    type: "error",
    title: "ax-devil",
    message: "Cannot open the server",
    detail: reason,
    buttons: ["Retry", "Return to local", "Quit"],
    defaultId: 0,
    cancelId: 1,
  };
  const { response } = await (parent && !parent.isDestroyed()
    ? dialog.showMessageBox(parent, options)
    : dialog.showMessageBox(options));
  return ["retry", "local", "quit"][response];
}

module.exports = { askAfterFailure, parseTarget, verifyServer };
