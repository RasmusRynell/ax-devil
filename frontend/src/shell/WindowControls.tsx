import { useEffect, useState } from "react";
import { IconButton } from "../kit/IconButton";
import "./windowBridge";

/** Minimize, maximize or restore, and close, drawn only inside the Electron shell, which frames no window itself. */
export function WindowControls() {
  const bridge = window.axDevilWindow;
  const [maximized, setMaximized] = useState(false);
  useEffect(() => bridge?.onMaximizedChange(setMaximized), [bridge]);
  if (!bridge) return null;
  // Out of the tab order, like the desktop's own title bar; Close turns red under the pointer.
  const size = "h-bar w-bar";
  const quiet = { tabIndex: -1 } as const;
  return (
    <div className="flex shrink-0 [-webkit-app-region:no-drag]">
      <IconButton
        icon="minus"
        label="Minimize"
        data-cursor-id="window:minimize"
        className={size}
        {...quiet}
        onClick={() => bridge.minimize()}
      />
      <IconButton
        icon={maximized ? "copy" : "square"}
        label={maximized ? "Restore" : "Maximize"}
        data-cursor-id="window:maximize"
        className={size}
        {...quiet}
        onClick={() => bridge.toggleMaximize()}
      />
      <IconButton
        icon="x"
        label="Close"
        data-cursor-id="window:close"
        className={`${size} enabled:hover:bg-close! enabled:hover:text-on-canvas!`}
        {...quiet}
        onClick={() => bridge.close()}
      />
    </div>
  );
}
