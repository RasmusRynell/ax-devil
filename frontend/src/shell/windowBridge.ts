import { useGrants } from "../access";

/** What the Electron shell's preload exposes to the page; absent in a plain browser. */
export interface WindowBridge {
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  /** Puts the whole window into or out of fullscreen. */
  toggleFullScreen(): void;
  /** Opens the native file dialog for video files. Resolves to the absolute paths chosen; none when cancelled. */
  pickVideos(): Promise<string[]>;
  /** Opens the native folder dialog. Resolves to the absolute path chosen; null when cancelled. */
  pickFolder(): Promise<string | null>;
  /**
   * Opens the native save dialog for an export, suggesting `name`. Resolves to the absolute path chosen, which ends in
   * `.extension`; null when cancelled.
   */
  pickExportFile(name: string, extension: "mp4" | "png"): Promise<string | null>;
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
  /** Closes the app and starts it again; only the local page may ask. Absent when the app did not start its backend. */
  restart?(): void;
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

/**
 * Where this page comes from: `browser` (no desktop shell around it), `local` (the desktop's own backend) or
 * `remote` (another machine's server, in the desktop). Only the local backend grants personal preferences, so the
 * grants tell the two desktop cases apart without asking the shell.
 */
export function useServerKind(): "browser" | "local" | "remote" {
  const grants = useGrants();
  if (!window.axDevilWindow) return "browser";
  return grants.includes("preferences") ? "local" : "remote";
}
