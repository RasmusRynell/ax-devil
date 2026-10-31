import type { MenuCommand } from "../kit/Menu";
import "./windowBridge";
import { MenuBar } from "./MenuBar";
import { WindowControls } from "./WindowControls";

interface Props {
  checked: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
}

/** The window's top bar: title, menus, an empty stretch that drags the window, and the window controls. */
export function TitleBar({ checked, onSelect }: Props) {
  return (
    <header className="flex h-bar shrink-0 items-center border-b border-line bg-panel [-webkit-app-region:drag]">
      <span className="hidden pl-m font-semibold whitespace-nowrap sm:inline">ax-devil</span>
      <MenuBar checked={checked} onSelect={onSelect} />
      <div
        className="h-full min-w-0 flex-1"
        onDoubleClick={() => window.axDevilWindow?.toggleMaximize()}
      />
      <WindowControls />
    </header>
  );
}
