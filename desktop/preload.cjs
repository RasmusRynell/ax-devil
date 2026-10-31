// The only bridge from the page to the window: minimize, maximize and close for the in-page title bar, and the
// native dialog that adds videos.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("axDevilWindow", {
  minimize: () => ipcRenderer.send("window:minimize"),
  toggleMaximize: () => ipcRenderer.send("window:toggle-maximize"),
  close: () => ipcRenderer.send("window:close"),
  // Opens the native file dialog; resolves to the chosen absolute paths, none when it is cancelled.
  pickVideos: () => ipcRenderer.invoke("workspace:pick-videos"),
  // Reports the current state at once and every change after; returns the way to stop listening.
  onMaximizedChange: (listener) => {
    const changed = (_event, maximized) => listener(maximized);
    ipcRenderer.on("window:maximized", changed);
    void ipcRenderer.invoke("window:is-maximized").then(listener);
    return () => ipcRenderer.off("window:maximized", changed);
  },
});
