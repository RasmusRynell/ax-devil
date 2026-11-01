// The only bridge from the page to the shell: the in-page title bar's window controls, the native dialogs that add
// videos and choose a folder, and moving the window between the local backend and another machine's server. Main
// answers only a window's trusted page.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("axDevilWindow", {
  minimize: () => ipcRenderer.send("window:minimize"),
  toggleMaximize: () => ipcRenderer.send("window:toggle-maximize"),
  close: () => ipcRenderer.send("window:close"),
  // Opens the native file dialog; resolves to the chosen absolute paths, none when it is cancelled.
  pickVideos: () => ipcRenderer.invoke("workspace:pick-videos"),
  // Opens the native folder dialog; resolves to the chosen absolute path, null when it is cancelled.
  pickFolder: () => ipcRenderer.invoke("settings:pick-folder"),
  // Reports the current state at once and every change after; returns the way to stop listening.
  onMaximizedChange: (listener) => {
    const changed = (_event, maximized) => listener(maximized);
    ipcRenderer.on("window:maximized", changed);
    void ipcRenderer.invoke("window:is-maximized").then(listener);
    return () => ipcRenderer.off("window:maximized", changed);
  },
  // Replaces this window with one on another machine's server (a server address or a join link); only the local
  // page may ask. Rejects when the address is not a web address.
  connect: (target, password) => ipcRenderer.invoke("window:connect", target, password),
  // Replaces this window with one on the local backend; only a remote page may ask.
  disconnect: () => ipcRenderer.invoke("window:disconnect"),
});
