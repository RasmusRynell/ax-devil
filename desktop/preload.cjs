// The only bridge from the page to the shell: the in-page title bar's window controls, the native dialogs that add
// videos, open and save workspaces, save an export and choose a folder, moving the window between the local backend
// and another machine's server, and restarting the app. Main answers only a window's trusted page.
const { contextBridge, ipcRenderer, webFrame } = require("electron");

// The shell names the windows that may restart the app (window.cjs; a sandboxed preload cannot require it).
const restartable = process.argv.includes("--ax-devil-restartable");

contextBridge.exposeInMainWorld("axDevilWindow", {
  minimize: () => ipcRenderer.send("window:minimize"),
  toggleMaximize: () => ipcRenderer.send("window:toggle-maximize"),
  close: () => ipcRenderer.send("window:close"),
  toggleFullScreen: () => ipcRenderer.send("window:toggle-fullscreen"),
  // Opens the native file dialog for a video; resolves to the chosen absolute path, null when it is cancelled.
  pickVideo: () => ipcRenderer.invoke("workspace:pick-file", "video"),
  // Opens the native file dialog for an overlay file, filtered to `extensions` (lowercase, with their dots); resolves
  // to the chosen absolute path, null when it is cancelled.
  pickOverlay: (extensions) => ipcRenderer.invoke("workspace:pick-file", "overlay", extensions),
  // Opens the native save dialog for an export (a suggested file name and `mp4` or `png`); resolves to the chosen
  // absolute path, null when it is cancelled.
  pickExportFile: (name, extension) => ipcRenderer.invoke("export:pick-file", name, extension),
  // Opens the native file dialog for a workspace to open; resolves to the chosen absolute path, null when cancelled.
  pickOpenWorkspace: () => ipcRenderer.invoke("workspace:pick-open"),
  // Opens the native save dialog for a workspace, suggesting `name` or, when the workspace has a file, `currentPath`;
  // resolves to the chosen absolute path, null when cancelled.
  pickSaveWorkspace: (name, currentPath) =>
    ipcRenderer.invoke("workspace:pick-save", name, currentPath),
  // Opens the native folder dialog, titled with `title` when given; resolves to the chosen absolute path, null when
  // it is cancelled.
  pickFolder: (title) => ipcRenderer.invoke("settings:pick-folder", title),
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
  // The desktop app's own version, which a remote server's page cannot learn from its backend.
  appVersion: () => ipcRenderer.invoke("app:version"),
  // Closes the app, which stops its backend, and starts it again; only the local page may ask. Absent when the app
  // did not start the backend it shows (one already running, say), which a restart would leave as it was.
  ...(restartable && { restart: () => ipcRenderer.send("app:restart") }),
  // The page zoom factor, 1 at 100%; overlays on the video keep their size when the page is zoomed.
  zoomFactor: () => webFrame.getZoomFactor(),
});
