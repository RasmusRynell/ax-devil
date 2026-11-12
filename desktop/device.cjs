// The settings that belong to this device (see docs/architecture/access.md): one file for the whole app, whichever
// server a window shows. A missing or unreadable file holds nothing, so the page uses its defaults.
const { readJson, writeJsonAsync } = require("./atomic-json.cjs");

/** Whether `value` is something a setting holds: text, a flag or a number. */
const plain = (value) => ["string", "boolean", "number"].includes(typeof value);

/**
 * Device settings read once. Writes are serialized and reject on failure; memory changes only after the
 * file was replaced. Raw values survive unrelated edits, including values the frontend cannot currently interpret.
 */
function createDeviceValues(file) {
  let values = {};
  try {
    const kept = readJson(file);
    if (kept && typeof kept === "object" && !Array.isArray(kept)) values = kept;
  } catch {
    // Nothing usable was kept.
  }
  let pending = Promise.resolve();
  let closing = false;
  return {
    read: () => ({ ...values }),
    write(changes) {
      if (closing) return Promise.reject(new Error("Device settings are closing"));
      if (
        !changes ||
        typeof changes !== "object" ||
        Array.isArray(changes) ||
        Object.values(changes).some((value) => value !== null && !plain(value))
      )
        return Promise.reject(new Error("Invalid Device settings"));
      const patch = { ...changes };
      const save = pending.then(async () => {
        const next = { ...values };
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) delete next[key];
          else next[key] = value;
        }
        await writeJsonAsync(file, next);
        values = next;
      });
      pending = save.catch(() => {});
      return save;
    },
    // Quit stops admission and drains admitted writes, even if their window has already closed.
    close() {
      closing = true;
      return pending;
    },
  };
}

module.exports = { createDeviceValues };
