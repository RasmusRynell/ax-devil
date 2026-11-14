// Fails when a JavaScript package installed for any workspace project (root, frontend, desktop) has a license
// outside the allow list in deny.toml.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");

const allowBlock = /^\[licenses\][\s\S]*?^allow = \[([\s\S]*?)\]/m.exec(
  fs.readFileSync("deny.toml", "utf8"),
);
const allowed = new Set([...allowBlock[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]));
// Evaluates an SPDX expression: OR binds looser than AND; WITH stays part of its license id.
const satisfied = (expression) => {
  const tokens = expression.match(/\(|\)|[^\s()]+(?:\s+WITH\s+[^\s()]+)?/g) ?? [];
  let next = 0;
  const any = () => {
    let result = all();
    while (tokens[next] === "OR") {
      next++;
      result = all() || result;
    }
    return result;
  };
  const all = () => {
    let result = one();
    while (tokens[next] === "AND") {
      next++;
      result = one() && result;
    }
    return result;
  };
  const one = () => {
    const token = tokens[next++];
    if (token !== "(") return allowed.has(token);
    const result = any();
    if (tokens[next++] !== ")") throw new Error(`Unbalanced license expression: ${expression}`);
    return result;
  };
  const result = any();
  if (next !== tokens.length) throw new Error(`Unsupported license expression: ${expression}`);
  return result;
};

const byLicense = JSON.parse(
  execFileSync("pnpm", ["licenses", "list", "--json", "--recursive"], { encoding: "utf8" }),
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
