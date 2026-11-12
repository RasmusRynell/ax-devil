// The Device owner announces persisted snapshots to every trusted window, independent of invoke replies.
function installDeviceIpc({ ipcMain, device, sender, windows }) {
  device.subscribe((snapshot) => {
    for (const window of windows()) {
      try {
        window.webContents.send("device:changed", snapshot);
      } catch {
        /* A window can close after enumeration; persistence already succeeded. */
      }
    }
  });
  ipcMain.on("device:values", (event) => {
    try {
      sender(event);
      event.returnValue = device.read();
    } catch {
      event.returnValue = { values: {}, sequence: 0 };
    }
  });
  ipcMain.handle("device:set", async (event, changes) => {
    try {
      sender(event);
      const sequence = await device.write(changes);
      return { sequence, reason: null };
    } catch (error) {
      return {
        sequence: error.sequence ?? null,
        reason: `Could not save the device settings: ${error.message}`,
      };
    }
  });
}
module.exports = { installDeviceIpc };
