import type { MenuCommand } from "../kit/Menu";
import { useDoc } from "../session/session";
import "./windowBridge";
import { MenuBar } from "./MenuBar";
import { ShareControl } from "./ShareControl";
import { UNTITLED, useWindowTitle } from "./useWindowTitle";
import { WindowControls } from "./WindowControls";

interface Props {
  checked: ReadonlySet<string>;
  /** The commands that do something today; the menus show only these. */
  wired: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
  /** Starts sharing the window's Session and shows the Sessions view. */
  onStartSharing: () => void;
}

/**
 * The workspace's name, `Untitled` without one, and a dot while it has unsaved changes; the window's title follows it. A narrow window leaves the
 * room to the menus, as it does for the app name, and keeps the dot.
 */
function WorkspaceName() {
  const workspace = useDoc()?.workspace;
  useWindowTitle(workspace);
  if (!workspace) return null;
  const name = workspace.name ?? UNTITLED;
  return (
    <span
      data-cursor-id="title:workspace"
      className="flex min-w-0 items-center gap-s px-m text-muted max-sm:px-0"
    >
      <span title={name} className="truncate max-sm:hidden">
        {name}
      </span>
      {workspace.modified && (
        <span role="img" aria-label="Modified" className="size-m shrink-0 rounded-full bg-accent" />
      )}
    </span>
  );
}

/** The window's top bar: title, menus, the workspace's name, an empty stretch that drags the window, the share entry and the window controls. */
export function TitleBar({ checked, wired, onSelect, onStartSharing }: Props) {
  return (
    <header
      data-cursor-region="titleBar"
      className="flex h-bar shrink-0 items-center border-b border-line bg-panel [-webkit-app-region:drag]"
    >
      <span
        data-cursor-id="title:name"
        className="hidden pl-m font-semibold whitespace-nowrap sm:inline"
      >
        ax-devil
      </span>
      <MenuBar checked={checked} wired={wired} onSelect={onSelect} />
      <WorkspaceName />
      <div
        data-cursor-id="title:drag"
        className="h-full min-w-0 flex-1"
        onDoubleClick={() => window.axDevilWindow?.toggleMaximize()}
      />
      <ShareControl onStartSharing={onStartSharing} />
      <WindowControls />
    </header>
  );
}
