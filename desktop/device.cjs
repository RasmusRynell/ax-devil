// The settings that belong to this device (see docs/architecture/access.md): one file for the whole app, whichever
// server a window shows. A missing or unreadable file holds nothing, so the page uses its defaults.
const fs = require("node:fs");
const path = require("node:path");

/** Whether `value` is something a setting holds: text, a flag or a number. */
const plain = (value) => ["string", "boolean", "number"].includes(typeof value);

/**
 * The device settings kept in `file`, read once. `read()` answers a copy of every value; `set(key, value)` stores one
 * and `set(key, null)` removes it, writing the file whole or not at all; a failed write is reported to the console and
 * leaves the values in memory.
 */
function createDeviceValues(file) {
  let values = {};
  try {
    const kept = JSON.parse(fs.readFileSync(file, "utf8"));
    if (kept && typeof kept === "object" && !Array.isArray(kept))
      values = Object.fromEntries(Object.entries(kept).filter(([, value]) => plain(value)));
  } catch {
    // Nothing usable was kept.
  }
  return {
    read: () => ({ ...values }),
    set(key, value) {
      if (typeof key !== "string" || (value !== null && !plain(value))) return false;
      values = { ...values };
      if (value === null) delete values[key];
      else values[key] = value;
      const partial = `${file}.partial`;
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(partial, JSON.stringify(values));
        fs.renameSync(partial, file);
      } catch (error) {
        console.error(`Could not save the device settings: ${error.message}`);
      }
      return true;
    },
  };
}

module.exports = { createDeviceValues };
