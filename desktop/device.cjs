// The settings that belong to this device (see docs/architecture/access.md): one file for the whole app, whichever
// server a window shows. A missing or unreadable file holds nothing, so the page uses its defaults.
const { readJson, writeJson } = require("./atomic-json.cjs");

/** Whether `value` is something a setting holds: text, a flag or a number. */
const plain = (value) => ["string", "boolean", "number"].includes(typeof value);

/**
 * Device settings read once. A save returns null on success or a reason on failure; memory changes only after the
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
  return {
    read: () => ({ ...values }),
    write(changes) {
      if (
        !changes ||
        typeof changes !== "object" ||
        Array.isArray(changes) ||
        Object.values(changes).some((value) => value !== null && !plain(value))
      )
        return "Invalid Device settings";
      const next = { ...values };
      for (const [key, value] of Object.entries(changes)) {
        if (value === null) delete next[key];
        else next[key] = value;
      }
      try {
        writeJson(file, next);
        values = next;
        return null;
      } catch (error) {
        return `Could not save the device settings: ${error.message}`;
      }
    },
  };
}

module.exports = { createDeviceValues };
