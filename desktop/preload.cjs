// The only bridge from the page to the shell: the in-page title bar's window controls, the native dialogs that add
// videos, open and save workspaces, save an export and choose a folder, this device's own settings, moving the window between the local backend
// and another machine's server, and restarting the app. A method exists only where its feature is offered: a window
// on another machine's server has none of this machine's native features and can only disconnect. Main answers only a
// window's trusted page.
const { contextBridge, ipcRenderer, webFrame } = require("electron");

// The shell names the windows that may restart the app and those that show another machine's server (window.cjs; a
// sandboxed preload cannot require it).
const restartable = process.argv.includes("--ax-devil-restartable");
const remote = process.argv.includes("--ax-devil-remote");

contextBridge.exposeInMainWorld("axDevilWindow", {
  ...(process.argv.includes("--ax-devil-restore-drag") && {
    restoreDrag: (phase, height) => {
      void ipcRenderer.invoke("window:restore-drag", phase, height);
    },
  }),
  minimize: () => ipcRenderer.send("window:minimize"),
  toggleMaximize: () => ipcRenderer.send("window:toggle-maximize"),
  close: () => ipcRenderer.send("window:close"),
  toggleFullScreen: () => ipcRenderer.send("window:toggle-fullscreen"),
  // This machine's native dialogs, only in a window on the local backend.
  ...(!remote && {
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
  }),
  // Reports the current state at once and every change after; returns the way to stop listening.
  onMaximizedChange: (listener) => {
    const changed = (_event, maximized) => listener(maximized);
    ipcRenderer.on("window:maximized", changed);
    void ipcRenderer.invoke("window:is-maximized").then(listener);
    return () => ipcRenderer.off("window:maximized", changed);
  },
  // Reports whether the window is fullscreen at once and on every change after; returns the way to stop listening.
  onFullScreenChange: (listener) => {
    const changed = (_event, fullscreen) => listener(fullscreen);
    ipcRenderer.on("window:fullscreen", changed);
    void ipcRenderer.invoke("window:is-fullscreen").then(listener);
    return () => ipcRenderer.off("window:fullscreen", changed);
  },
  ...(!remote && {
    // Replaces this window with one on another machine's server (a server address or a join link);
    // rejects when the address is not a web address.
    connect: (target, password) => ipcRenderer.invoke("window:connect", target, password),
    // Opens the folder of the log file at `file` (the absolute path the backend reports) in the file manager; rejects
    // for any other file name.
    openLogs: (file) => ipcRenderer.invoke("logs:open", file),
  }),
  ...(remote && {
    // Replaces this window with one on the local backend; only a remote page has it.
    disconnect: () => ipcRenderer.invoke("window:disconnect"),
  }),
  // The desktop app's own version, which a remote server's page cannot learn from its backend.
  appVersion: () => ipcRenderer.invoke("app:version"),
  // Closes the app, which stops its backend, and starts it again. Absent when the app did not start the backend it
  // shows (one already running, say), which a restart would leave as it was.
  ...(restartable && { restart: () => ipcRenderer.send("app:restart") }),
  // The settings of this device, one file for the app whichever server the window shows: every value now, read before
  // the page paints.
  deviceValues: () => ipcRenderer.sendSync("device:values"),
  // Stores one device setting, or removes it with null.
  setDeviceValues: (changes) => ipcRenderer.sendSync("device:set", changes),
  // Reports every value after another window changed one; returns the way to stop listening.
  onDeviceValuesChange: (listener) => {
    const changed = (_event, values) => listener(values);
    ipcRenderer.on("device:changed", changed);
    return () => ipcRenderer.off("device:changed", changed);
  },
  // The page zoom factor, 1 at 100%; overlays on the video keep their size when the page is zoomed.
  zoomFactor: () => webFrame.getZoomFactor(),
});
