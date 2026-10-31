/** What the Electron shell's preload exposes to the title bar; absent in a plain browser. */
export interface WindowBridge {
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  /** Reports the current state at once and every change after; returns the way to stop listening. */
  onMaximizedChange(listener: (maximized: boolean) => void): () => void;
}

declare global {
  interface Window {
    axDevilWindow?: WindowBridge;
  }
}
