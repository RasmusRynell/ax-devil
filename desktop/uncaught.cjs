// What the main process does with an error nobody caught: log it, tell the user and, on their say, go on. A page's own
// uncaught errors are the page's business (frontend/src/shell/UncaughtErrors.tsx).

// Errors handled within the forgetting time before the next one ends the app.
const MAX_RECENT = 10;
const FORGET_MS = 5000;

/**
 * Returns the handler for an uncaught error; it resolves once the error has been dealt with. `show(summary, details)`
 * resolves to whether to continue; `log(text)` records; `quit(status)` ends the app. Dialogs come one at a time, and an
 * error waiting for its turn counts as handled. A handled error is forgotten `FORGET_MS` after its dialog closes, and
 * one that arrives with `MAX_RECENT` still remembered ends the app with status 1, since the same failure is repeating.
 */
function uncaughtHandler({ show, log, quit }) {
  let recent = 0;
  let turn = Promise.resolve();
  return (error) => {
    if (recent >= MAX_RECENT) {
      log("Too many exceptions occurred, terminating application");
      quit(1);
      return Promise.resolve();
    }
    recent++;
    const failure = error instanceof Error ? error : new Error(String(error));
    log(`Uncaught exception: ${failure.stack ?? failure.message}`);
    turn = turn.then(async () => {
      try {
        const keepGoing = await show(`${failure.name}: ${failure.message}`, failure.stack ?? "");
        if (!keepGoing) quit(0);
      } catch (shown) {
        log(`Could not show the error: ${String(shown)}`);
      } finally {
        setTimeout(() => recent--, FORGET_MS).unref();
      }
    });
    return turn;
  };
}

/** Handles uncaught exceptions and unhandled rejections in the main process with a native dialog. */
function installUncaughtHandler({ app, dialog }) {
  const handle = uncaughtHandler({
    show: async (summary, details) => {
      const { response } = await dialog.showMessageBox({
        type: "error",
        title: "Application Error",
        message: `An unexpected error occurred:\n\n${summary}`,
        detail: `ax-devil will try to continue, but some features may fail.\n\n${details}`,
        buttons: ["Continue", "Close"],
        defaultId: 0,
        cancelId: 0,
      });
      return response === 0;
    },
    log: (text) => console.error(text),
    quit: (status) => (status ? app.exit(status) : app.quit()),
  });
  process.on("uncaughtException", (error) => void handle(error));
  process.on("unhandledRejection", (reason) => void handle(reason));
}

module.exports = { installUncaughtHandler, uncaughtHandler };
