import type { MenuCommand } from "../kit/Menu";
import { useDoc } from "../session/session";
import "../client/windowBridge";
import { MenuBar } from "./MenuBar";
import { ShareControl } from "./ShareControl";
import { useWindowTitle } from "./useWindowTitle";
import { WindowControls } from "./WindowControls";

interface Props {
  checked: ReadonlySet<string>;
  /** The commands that do something today; the menus show only these. */
  wired: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
  /** Starts sharing the window's Session and shows the Sessions view. */
  onStartSharing: () => void;
}

/** The window's top bar: the app's name, menus, an empty stretch that drags the window, the share entry and the window controls. */
export function TitleBar({ checked, wired, onSelect, onStartSharing }: Props) {
  // The desktop window's title follows the workspace; the bar keeps the app's name.
  useWindowTitle(useDoc()?.workspace);
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
