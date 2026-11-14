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

/** Device persistence uses the same strict replacement sequence without blocking the shell's event loop. */
async function writeJsonAsync(file, values, signal) {
  const check = () => signal?.throwIfAborted();
  check();
  const modeOf = async () => {
    try {
      const previous = await fs.promises.lstat(file);
      if (!previous.isFile()) throw new Error("Settings target must be a regular file");
      return previous.mode & 0o777;
    } catch (error) {
      if (error.code === "ENOENT") return 0o600;
      throw error;
    }
  };
  const mode = await modeOf();
  check();
  await fs.promises.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  check();
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${randomUUID()}.tmp`);
  let descriptor;
  let owned = false;
  try {
    descriptor = await fs.promises.open(
      temporary,
      fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL,
      0o600,
    );
    owned = true;
    check();
    await descriptor.writeFile(JSON.stringify(values));
    check();
    await descriptor.chmod(mode & (await modeOf()));
    check();
    await descriptor.sync();
    check();
    await descriptor.close();
    descriptor = undefined;
    check();
    await fs.promises.rename(temporary, file);
    owned = false;
    check();
  } finally {
    try {
      if (descriptor !== undefined) await descriptor.close();
    } finally {
      if (owned) await fs.promises.unlink(temporary);
    }
  }
}

/** Read only a regular target, avoiding symlinks and blocking special files. */
function readJson(file) {
  targetMode(file);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

module.exports = { readJson, writeJson, writeJsonAsync };
