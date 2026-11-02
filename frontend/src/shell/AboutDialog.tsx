import { useEffect, useState } from "react";
import { Dialog } from "../kit/Dialog";
import { useServerKind } from "./windowBridge";

/** Where the project lives; the desktop shell opens exactly this link in the default browser. */
const PROJECT_LINK = "https://github.com/rasmusrynell/ax-devil";

/**
 * The About dialog: the app's name, its version, what it is for and where the project lives. In the desktop app the
 * version is the app's own; a window on another machine's server also names that server and its version. In a browser
 * the app is the backend, so its version is the one shown.
 */
export function AboutDialog({ version, onClose }: { version: string; onClose: () => void }) {
  const kind = useServerKind();
  const [desktop, setDesktop] = useState<string | null>(null);
  useEffect(() => {
    void window.axDevilWindow?.appVersion().then(setDesktop);
  }, []);
  // The desktop's version is asked for; until it answers there is no version to show.
  const shown = kind === "browser" ? version : desktop;
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
    </Dialog>
  );
}
