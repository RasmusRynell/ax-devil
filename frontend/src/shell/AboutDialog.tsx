import { useEffect, useState } from "react";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";
import { useSettings } from "../settings";
import { useServerKind } from "./windowBridge";

/** Where the project lives; the desktop shell opens exactly this link in the default browser. */
const PROJECT_LINK = "https://github.com/rasmusrynell/ax-devil";

/**
 * The About dialog: the app's name, its version, what it is for and where the project lives. In the desktop app the
 * version is the app's own; a window on another machine's server also names that server and its version. In a browser
 * the app is the backend, so its version is the one shown. Whoever may see this machine's files can also copy what a
 * bug report needs, and in the desktop app open the folder of the log.
 */
export function AboutDialog({ version, onClose }: { version: string; onClose: () => void }) {
  const kind = useServerKind();
  const { file } = useSettings();
  const [desktop, setDesktop] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    void window.axDevilWindow?.appVersion().then(setDesktop);
  }, []);
  // The desktop's version is asked for; until it answers there is no version to show.
  const shown = kind === "browser" ? version : desktop;
  const bridge = kind === "local" ? window.axDevilWindow : undefined;
  const log = file?.log;
  // A page that is not a secure context, such as a server's over plain HTTP, has no clipboard.
  const canCopy = typeof navigator.clipboard?.writeText === "function";
  const failed = (error: unknown) => setProblem(error instanceof Error ? error.message : "Failed");
  const copy = () => {
    if (!file) return;
    const text = [
      `ax-devil ${shown ?? version}`,
      `Backend ${version}`,
      navigator.userAgent,
      `Settings ${file.path} (version ${file.version})`,
      ...(log === undefined ? [] : [`Log ${log}`]),
    ].join("\n");
    setProblem(null);
    navigator.clipboard.writeText(text).catch(failed);
  };
  return (
    <Dialog title="About ax-devil" onOk={onClose} focusOk>
      {shown && <p className="m-0 text-accent">Version {shown}</p>}
      {kind === "remote" && (
        <p className="m-0 text-muted">
          Connected to {window.location.host} (version {version})
        </p>
      )}
      <p className="m-0 max-w-[40ch]">
        Inspect Axis camera streams, offline video, and analytics overlays.
      </p>
      <a
        href={PROJECT_LINK}
        target="_blank"
        rel="noreferrer noopener"
        className="w-fit rounded-control text-accent underline focus-ring"
      >
        github.com/rasmusrynell/ax-devil
      </a>
      {file && (canCopy || (bridge && log !== undefined)) && (
        <div className="flex flex-wrap gap-s">
          {bridge && log !== undefined && (
            <Button onClick={() => bridge.openLogs(log).catch(failed)}>Open logs folder</Button>
          )}
          {canCopy && <Button onClick={copy}>Copy diagnostics</Button>}
        </div>
      )}
      {problem && <span role="alert">{problem}</span>}
    </Dialog>
  );
}
