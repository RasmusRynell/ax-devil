import { Dialog } from "../kit/Dialog";

/** Where the project lives; the desktop shell opens exactly this link in the default browser. */
const PROJECT_LINK = "https://github.com/rasmusrynell/ax-devil";

/** The About dialog: the app's name, the backend's version, what it is for and where the project lives. */
export function AboutDialog({ version, onClose }: { version: string; onClose: () => void }) {
  return (
    <Dialog title="About ax-devil" onOk={onClose} focusOk>
      <p className="m-0 text-accent">Version {version}</p>
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
