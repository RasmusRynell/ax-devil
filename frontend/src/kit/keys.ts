import type { KeyboardEvent } from "react";

/**
 * A key handler that keeps a control's own keys from reaching the shell's shortcuts, so Space on a chip chooses it
 * instead of playing the video and an arrow key moves the choice instead of stepping a frame. Keys with Ctrl, Alt or
 * Meta held still pass through.
 */
export function ownKeys(keys: ReadonlySet<string>) {
  return (event: KeyboardEvent) => {
    if (keys.has(event.key) && !event.ctrlKey && !event.altKey && !event.metaKey)
      event.stopPropagation();
  };
}
