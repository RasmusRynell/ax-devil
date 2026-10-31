// Rejects authored files too large for an agent to read comfortably: 5,000 tokens, counted as bytes / 4.
// Checks every tracked and untracked, non-ignored file; with --changed, only those changed since HEAD.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MAX_TOKENS = 5000;
const BYTES_PER_TOKEN = 4;
// Lockfiles, generated files, data and binaries are not authored by hand or by agents. Built-in render catalogs are
// loaded whole as one JSON document; splitting them would need an include format in the catalog language.
const EXEMPT = [
  /(^|\/)(Cargo\.lock|pnpm-lock\.yaml)$/,
  /^frontend\/src\/generated\//,
  /^assets\/catalogs\/[^/]+\.json$/,
  /^fixtures\//,
  /\.(png|jpe?g|gif|webp|ico|svg|mp4|mkv|mov|h26[45]|pdf|ttf|otf|woff2?|zip|gz|tar|wasm|bin)$/i,
];

const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).split("\n");
const files = process.argv.includes("--changed")
  ? [...git("diff", "--name-only", "HEAD"), ...git("ls-files", "-o", "--exclude-standard")]
  : git("ls-files", "-co", "--exclude-standard");
const tooLarge = [];
for (const file of files) {
  const name = file.split(path.sep).join("/");
  if (!name || EXEMPT.some((pattern) => pattern.test(name))) continue;
  let stat;
  try {
    stat = fs.statSync(file);
  } catch {
    continue; // Deleted in the working tree.
  }
  if (!stat.isFile()) continue;
  const tokens = Math.ceil(stat.size / BYTES_PER_TOKEN);
  if (tokens > MAX_TOKENS) tooLarge.push(`${name}: ~${tokens} tokens`);
}

if (tooLarge.length) {
  console.error(`Files over ${MAX_TOKENS} tokens (~${MAX_TOKENS * BYTES_PER_TOKEN} bytes):
  ${tooLarge.join("\n  ")}

Split each one along its seams, aiming under ~${(MAX_TOKENS * 4) / 5} tokens per file:
  - Code: group top-level items by responsibility and move each group whole into a sibling module named for it;
    update the call sites instead of re-exporting.
  - Markdown: split by top-level heading and link the parts from the original, which becomes an index. Prefer
    deleting redundant text over moving it (AGENTS.md, "Where A Fact Belongs").
  - Tests: split by the unit under test.
Exempt a file in scripts/check-file-tokens.cjs only when it is generated, a lockfile or data.`);
  process.exit(1);
}
