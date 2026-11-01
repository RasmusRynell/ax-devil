// What starts the shell on a server: `--server URL` and `ax-devil://open?url=<encoded http(s) link>`, from the
// command line, from a second launch, or from the operating system. Links are confirmed; `--server` is the user's own.
const { app, dialog } = require("electron");

const { parseLaunch } = require("./args.cjs");

/** The http(s) link inside an `ax-devil://open?url=...` link; throws when it is anything else. */
function linkTarget(link) {
  let target;
  try {
    const url = new URL(link);
    if (url.hostname === "open") target = url.searchParams.get("url");
    if (target && !/^https?:$/.test(new URL(target).protocol)) target = null;
  } catch {
    target = null;
  }
  if (!target) throw new Error("This ax-devil link does not hold a web address to open.");
  return target;
}

/** Asks whether to open a link's server; the link's secrets stay out of the question. */
async function confirmLink(target) {
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
 * instance runs, which receives this launch instead. Call before the app is ready.
 */
function singleInstance(onLaunch) {
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
    onLaunch({ link: url });
  });
  return true;
}

module.exports = { confirmLink, linkTarget, singleInstance };
