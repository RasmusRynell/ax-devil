const fs = require("node:fs");

/** Whether a smoke verdict can pass, independently of a platform's process exit-code conventions. */
function passed({ expired, stopped, verdict }) {
  return !expired && !stopped && verdict === "PASS";
}
module.exports = { passed };
if (require.main === module) {
  const [expired, stopped, file] = process.argv.slice(2);
  let verdict = "";
  try {
    verdict = fs.readFileSync(file, "utf8").trim();
  } catch {
    /* Missing verdict fails. */
  }
  process.exitCode = passed({ expired: expired === "1", stopped: stopped === "1", verdict })
    ? 0
    : 1;
}
