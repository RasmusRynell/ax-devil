import { useEffect, useState } from "react";
import { IconButton } from "../kit/IconButton";
import "./windowBridge";

/** Minimize, maximize or restore, and close, drawn only inside the Electron shell, which frames no window itself. */
export function WindowControls() {
  const bridge = window.axDevilWindow;
  const [maximized, setMaximized] = useState(false);
  useEffect(() => bridge?.onMaximizedChange(setMaximized), [bridge]);
  if (!bridge) return null;
  const size = "h-bar w-bar";
  return (
    <div className="flex shrink-0 [-webkit-app-region:no-drag]">
      <IconButton
        icon="minus"
        label="Minimize"
        className={size}
        onClick={() => bridge.minimize()}
      />
      <IconButton
        icon="square"
        label={maximized ? "Restore" : "Maximize"}
        className={size}
        onClick={() => bridge.toggleMaximize()}
      />
      <IconButton icon="x" label="Close" className={size} onClick={() => bridge.close()} />
    </div>
  );
}
