// The settings that belong to this device (see docs/architecture/access.md): one file for the whole app, whichever
// server a window shows. A missing or unreadable file holds nothing, so the page uses its defaults.
const { readJson, writeJsonAsync } = require("./atomic-json.cjs");

/** Whether `value` is something a setting holds: text, a flag or a number. */
const plain = (value) => ["string", "boolean", "number"].includes(typeof value);

const DEVICE_DRAIN_MS = 2000;

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
  let sequence = 0;
  let admitted = 0;
  const listeners = new Set();
  const abort = new AbortController();
  let expire;
  const expired = new Promise((_, reject) => {
    expire = reject;
  });
  void expired.catch(() => {});
  let closed;
  const snapshot = () => ({ values: { ...values }, sequence });
  let pending = Promise.resolve();
  let closing = false;
  return {
    read: snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    write(changes, batch) {
      if (closing) return Promise.reject(new Error("Device settings are closing"));
      if (
        !changes ||
        typeof changes !== "object" ||
        Array.isArray(changes) ||
        Object.values(changes).some((value) => value !== null && !plain(value))
      )
        return Promise.reject(new Error("Invalid Device settings"));
      const patch = { ...changes };
      const mine = ++admitted;
      const save = pending.then(async () => {
        abort.signal.throwIfAborted();
        const next = { ...values };
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) delete next[key];
          else next[key] = value;
        }
        await writeJsonAsync(file, next, abort.signal);
        abort.signal.throwIfAborted();
        values = next;
        sequence = mine;
        for (const listener of listeners) listener({ ...snapshot(), ...(batch && { batch }) });
        return mine;
      });
      pending = save.catch(() => {});
      return Promise.race([save, expired]).catch((error) => {
        throw Object.assign(new Error(error.message), { sequence: mine });
      });
    },
    // Quit stops admission and drains admitted writes, even if their window has already closed.
    close() {
      if (closed) return closed;
      closing = true;
      closed = new Promise((resolve) => {
        const timer = setTimeout(() => {
          const error = new Error("Device shutdown deadline expired");
          abort.abort(error);
          expire(error);
          resolve();
        }, DEVICE_DRAIN_MS);
        void pending.then(() => {
          clearTimeout(timer);
          resolve();
        });
      });
      return closed;
    },
  };
}

module.exports = { createDeviceValues, DEVICE_DRAIN_MS };
