// Shared shell JSON storage. Parent directory symlinks retain their existing behavior.
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

/** A missing target is new; anything present must be a regular file, never a link. */
function targetMode(file) {
  try {
    const previous = fs.lstatSync(file);
    if (!previous.isFile()) throw new Error("Settings target must be a regular file");
    return previous.mode & 0o777;
  } catch (error) {
    if (error.code === "ENOENT") return 0o600;
    throw error;
  }
}

/** Replace a regular target using an exclusively created, private file in the same directory. */
function writeJson(file, values) {
  const mode = targetMode(file);
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${randomUUID()}.tmp`);
  let descriptor;
  let owned = false;
  try {
    descriptor = fs.openSync(
      temporary,
      fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL,
      0o600,
    );
    owned = true;
    fs.writeFileSync(descriptor, JSON.stringify(values));
    // Re-check the target before replacement; never widen permissions if they were tightened meanwhile.
    fs.fchmodSync(descriptor, mode & targetMode(file));
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    fs.renameSync(temporary, file);
    owned = false;
  } finally {
    try {
      if (descriptor !== undefined) fs.closeSync(descriptor);
    } finally {
      if (owned) fs.unlinkSync(temporary);
    }
  }
}

/** Read only a regular target, avoiding symlinks and blocking special files. */
function readJson(file) {
  targetMode(file);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

module.exports = { readJson, writeJson };
