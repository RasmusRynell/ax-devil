// What starts the shell on a server: `--server URL` and `ax-devil://open?url=<encoded http(s) link>`, from the
// command line, from a second launch, or from the operating system. Links are confirmed; `--server` is the user's own.
// Electron is loaded where it is used, so a test that passes its own `app` and `dialog` needs no Electron.
const electron = () => require("electron");

const { parseLaunch } = require("./args.cjs");

const SCHEME = "ax-devil";

/** The http(s) link inside an `ax-devil://open?url=...` link; throws when it is anything else. */
function linkTarget(link) {
  let target;
  try {
    const url = new URL(link);
    if (url.protocol === `${SCHEME}:` && url.hostname === "open")
      target = url.searchParams.get("url");
    if (target && !/^https?:$/.test(new URL(target).protocol)) target = null;
  } catch {
    target = null;
  }
  if (!target) throw new Error("This ax-devil link does not hold a web address to open.");
  return target;
}

/** Asks whether to open a link's server; the link's secrets stay out of the question. */
async function confirmLink(target) {
  const { dialog } = electron();
  const { host, origin } = new URL(target);
  const { response } = await dialog.showMessageBox({
    type: "question",
    title: "ax-devil",
    message: `Open session on ${host}?`,
    detail: `ax-devil will load ${origin}.`,
    buttons: ["Open", "Cancel"],
    defaultId: 1,
    cancelId: 1,
  });
  return response === 0;
}

/**
 * Keeps one app instance and routes every launch to `onLaunch(launch)`, with `launch.link` or `launch.server` set
 * when it carries one: later launches (`second-instance`) and macOS `open-url` events. Returns false when another
 * instance runs, which receives this launch instead. Call before the app is ready. `app` and `dialog` are a test seam.
 */
function singleInstance(onLaunch, { app, dialog } = electron()) {
  if (!app.requestSingleInstanceLock()) return false;
  app.on("second-instance", (_event, argv) => {
    try {
      onLaunch(parseLaunch(argv).launch);
    } catch (error) {
      dialog.showErrorBox("ax-devil could not use its arguments", error.message);
    }
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    // Only the app's own links; anything else the system hands over is not a launch.
    if (url.startsWith(`${SCHEME}://`)) onLaunch({ link: url });
  });
  return true;
}

/**
 * Makes the operating system hand `ax-devil://` links to the installed app. Only a packaged app registers: a
 * development shell must not take the link away from the installed one. A refusal by the system is the app's loss
 * of links only, so it is reported to `report` and nothing else. `app` is a test seam.
 */
function registerLinks(report, { app } = electron()) {
  if (!app.isPackaged) return;
  if (!app.setAsDefaultProtocolClient(SCHEME))
    report(new Error(`The system did not register ${SCHEME}:// links for this app.`));
}

module.exports = { confirmLink, linkTarget, registerLinks, singleInstance };
