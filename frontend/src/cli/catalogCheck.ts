// `ax-devil catalog check FILE [NAME=PATH]...` runs this bundle in Electron-as-Node: the same compiler the app
// uses, no window. Each NAME=PATH names an example to draw the catalog on; PATH holds the raw bytes of its Sample
// Item (the columns the viewer receives: magic, version and columns, little-endian, a multiple of 8 bytes long).
import { readFileSync } from "node:fs";
import { type Sheet, checkCatalog } from "../present/catalog/check";

/** The bytes of `path`, or the reason they cannot be read, named after what was being read. */
function read(what: string, path: string): Buffer {
  try {
    return readFileSync(path);
  } catch (error) {
    throw new Error(
      `Cannot read ${what}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

const [file, ...named] = process.argv.slice(2);
let report;
try {
  const sheets: Sheet[] = named.map((argument) => {
    const split = argument.indexOf("=");
    if (split <= 0) throw new Error(`'${argument}' is not NAME=PATH`);
    const name = argument.slice(0, split);
    return {
      name,
      item: new Uint8Array(
        read(`${name} (${argument.slice(split + 1)})`, argument.slice(split + 1)),
      ),
    };
  });
  report = checkCatalog(read(file, file).toString("utf8"), file, sheets);
} catch (error) {
  report = { ok: false, text: `${error instanceof Error ? error.message : String(error)}\n` };
}
process.stdout.write(report.text);
process.exitCode = report.ok ? 0 : 1;
