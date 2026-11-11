import { useEffect, useState } from "react";

/** What the Electron shell's preload exposes to the page; absent in a plain browser. */
export interface WindowBridge {
  /** Whether this window shows another machine's server, whose machine the native features below cannot reach. */
  remote: boolean;
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  /** Puts the whole window into or out of fullscreen. */
  toggleFullScreen(): void;
  /** Reports whether the window is fullscreen at once and on every change after; returns the way to stop listening. */
  onFullScreenChange(listener: (fullscreen: boolean) => void): () => void;
  /** Opens the native file dialog for one video file. Resolves to the absolute path chosen; null when cancelled. */
  pickVideo(): Promise<string | null>;
  /**
   * Opens the native file dialog for one overlay file, filtered to `extensions` (each lowercase with its dot, as a
   * decoder declares it; none leaves every file). Resolves to the absolute path chosen; null when cancelled.
   */
  pickOverlay(extensions: readonly string[]): Promise<string | null>;
  /** Opens the native folder dialog titled `title`, which names what is chosen. Resolves to the absolute path chosen; null when cancelled. */
  pickFolder(title?: string): Promise<string | null>;
  /**
   * Opens the native save dialog for an export, suggesting `name`. Resolves to the absolute path chosen, which ends in
   * `.extension`; null when cancelled.
   */
  pickExportFile(name: string, extension: "mp4" | "png" | "csv"): Promise<string | null>;
  /** Opens the native file dialog for a saved workspace. Resolves to the absolute path chosen; null when cancelled. */
  pickOpenWorkspace(): Promise<string | null>;
  /**
   * Opens the native save dialog for a workspace, suggesting `name`, or `currentPath` when it has a file. Resolves to
   * the absolute path chosen, with the workspace suffix; null when cancelled.
   */
  pickSaveWorkspace(name: string, currentPath: string | null): Promise<string | null>;
  /** Reports the current state at once and every change after; returns the way to stop listening. */
  onMaximizedChange(listener: (maximized: boolean) => void): () => void;
  /**
   * Replaces this window with one on another machine's server, given its address or a join link; only the local page
   * may ask. Rejects with the reason when the address is not a web address.
   */
  connect(target: string, password?: string): Promise<void>;
  /** Replaces this window with one on the local backend; only a remote page may ask. */
  disconnect(): Promise<void>;
  /** The desktop app's own version. */
  appVersion(): Promise<string>;
  /** Opens the folder of the log file at this absolute path in the file manager; only the local page may ask. */
  openLogs(file: string): Promise<void>;
  /** Closes the app and starts it again; only the local page may ask. Absent when the app did not start its backend. */
  restart?(): void;
  /** Every setting this device keeps, one file whichever server the window shows; read before the page paints. */
  deviceValues?(): Record<string, unknown>;
  /** Stores one device setting, or removes it with null. */
  setDeviceValue?(key: string, value: string | boolean | number | null): void;
  /** Reports every device setting after another window changed one; returns the way to stop listening. */
  onDeviceValuesChange?(listener: (values: Record<string, unknown>) => void): () => void;
  /** The page zoom factor (1 is 100%), read when asked. */
  zoomFactor?(): number;
}

declare global {
  interface Window {
    axDevilWindow?: WindowBridge;
  }
}

/** Puts the whole application into or out of fullscreen: the window in the desktop, the page in a browser. */
export function toggleAppFullscreen(): void {
  if (window.axDevilWindow) window.axDevilWindow.toggleFullScreen();
  else if (document.fullscreenElement === document.documentElement) void document.exitFullscreen();
  else {
    // A lane or another element may be fullscreen: it gives way to the page. A refused request leaves things as they are.
    void Promise.resolve(document.fullscreenElement && document.exitFullscreen())
      .then(() => document.documentElement.requestFullscreen())
      .catch(() => {});
  }
}

/** Whether the whole application is fullscreen, however it got there: the window in the desktop, the page in a browser. */
export function useAppFullscreen(): boolean {
  const bridge = window.axDevilWindow;
  const [fullscreen, setFullscreen] = useState(
    !bridge && document.fullscreenElement === document.documentElement,
  );
  useEffect(() => {
    if (bridge) return bridge.onFullScreenChange(setFullscreen);
    const changed = () => setFullscreen(document.fullscreenElement === document.documentElement);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, [bridge]);
  return fullscreen;
}

/**
 * Where this page comes from: `browser` (no desktop shell around it), `local` (the desktop's own backend) or
 * `remote` (another machine's server, in the desktop). The shell says which; the role never does.
 */
export function useServerKind(): "browser" | "local" | "remote" {
  if (!window.axDevilWindow) return "browser";
  return window.axDevilWindow.remote ? "remote" : "local";
}

/** The shell's native features (file dialogs, restart, opening logs), only in a window on this machine's own backend. */
export function nativeBridge(): WindowBridge | undefined {
  const bridge = window.axDevilWindow;
  return bridge && !bridge.remote ? bridge : undefined;
}
