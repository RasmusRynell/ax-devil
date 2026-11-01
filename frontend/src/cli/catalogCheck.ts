// `ax-devil catalog check FILE` runs this bundle in Electron-as-Node: the same compiler the app uses, no window.
import { readFileSync } from "node:fs";
import { checkCatalog } from "../present/catalog/check";

const file = process.argv[2];
let report;
try {
  report = checkCatalog(readFileSync(file, "utf8"), file);
} catch (error) {
  const reason = error instanceof Error ? error.message : String(error);
  report = { ok: false, text: `Cannot read ${file}: ${reason}\n` };
}
process.stdout.write(report.text);
process.exitCode = report.ok ? 0 : 1;
