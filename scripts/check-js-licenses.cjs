// Fails when an installed JavaScript package's license is outside the allow list in deny.toml.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");

const allowBlock = /^\[licenses\][\s\S]*?^allow = \[([\s\S]*?)\]/m.exec(
  fs.readFileSync("deny.toml", "utf8"),
);
const allowed = new Set([...allowBlock[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]));
const satisfied = (expression) => {
  const bare = expression.replace(/^\((.*)\)$/, "$1").trim();
  if (bare.includes(" OR ")) return bare.split(" OR ").some(satisfied);
  if (bare.includes(" AND ")) return bare.split(" AND ").every(satisfied);
  return allowed.has(bare);
};

const byLicense = JSON.parse(
  execFileSync("pnpm", ["licenses", "list", "--json"], { encoding: "utf8" }),
);
const rejected = Object.entries(byLicense)
  .filter(([license]) => !satisfied(license))
  .flatMap(([license, packages]) =>
    packages.map((pkg) => `${pkg.name}@${pkg.versions.join(",")}: ${license}`),
  );
if (rejected.length) {
  console.error(`JavaScript packages with licenses outside deny.toml:\n  ${rejected.join("\n  ")}`);
  process.exit(1);
}
