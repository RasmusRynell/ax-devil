// Another machine's server: what to load, whether this shell may load it, and what to ask when that fails.
// Electron is loaded where it is used, so a test of the pure parts needs none.

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

// Why a connection fails, in the words of someone joining a share from the same network, by the code Node (fetch) or
// Chromium (a page) reports. The code stays in the reason for whoever has to look it up.
const REASONS = {
  ECONNREFUSED: "nothing is listening there; the host may have stopped sharing",
  ERR_CONNECTION_REFUSED: "nothing is listening there; the host may have stopped sharing",
  TimeoutError: "no answer; check the address and that both machines share a network",
  ETIMEDOUT: "no answer; check the address and that both machines share a network",
  ERR_CONNECTION_TIMED_OUT: "no answer; check the address and that both machines share a network",
  EHOSTUNREACH: "this network cannot reach the host",
  ENETUNREACH: "this network cannot reach the host",
  ERR_ADDRESS_UNREACHABLE: "this network cannot reach the host",
  ERR_INTERNET_DISCONNECTED: "this machine is offline",
  ENOTFOUND: "the host name is unknown",
  EAI_AGAIN: "the host name is unknown",
  ERR_NAME_NOT_RESOLVED: "the host name is unknown",
  ECONNRESET: "the host closed the connection",
  ERR_CONNECTION_RESET: "the host closed the connection",
};

/** Why a connection failed: `failure` is a thrown `fetch` error or a Chromium error name such as `ERR_CONNECTION_REFUSED`. */
function explainFailure(failure) {
  const detail = typeof failure === "string" ? failure.replace(/^net::/, "") : undefined;
  const code = detail ?? failure.cause?.code ?? failure.name;
  const reason = REASONS[code];
  if (reason) return `${reason} (${code})`;
  return detail ?? failure.cause?.code ?? failure.cause?.message ?? failure.message;
}

/** Rejects, saying why, unless `base` is an ax-devil server whose frontend needs the shell interface this app has. */
async function verifyServer(base) {
  let info;
  try {
    info = await fetchInfo(base, 5000);
  } catch (error) {
    throw new Error(`Cannot reach ${base}: ${explainFailure(error)}`);
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
  const { dialog } = require("electron");
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

module.exports = { askAfterFailure, explainFailure, parseTarget, verifyServer };
