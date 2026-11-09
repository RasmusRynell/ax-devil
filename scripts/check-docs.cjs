// Enforces the rules of docs/parity.md "Row format" on docs/parity/*.md, and that every relative markdown link in the
// repository resolves. Reads the file list from git, the Makefile, docs/parity.md, docs/parity/*.md and
// docs/rebuild/01-product-scope.md. Rules: P1 index and file pairs, P2 row shape, P3 row IDs and priorities against 01,
// P4 Test cells name files or Makefile targets that exist, P5 deviations name known rows of their area, and every
// dropped M row has one, L1 links. Left to review: that a dropped M row's deviation records the owner's written
// decision, and where a deviation naming only reference IDs is filed.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const STATUSES = new Set(["todo", "in progress", "done", "dropped"]);
const PRIORITIES = new Set(["M", "S"]);
const TEST_ROOTS = ["", "crates/", "frontend/src/"];
const ROW_ID = /^[A-Z]{2}-\d{2}$/;
// Anything shaped like an ID, and the reference-behavior IDs the deviations use (`VPC-009`, `LVV-A002`).
const ID_LIKE = /\b[A-Z][A-Z0-9]*-[A-Z0-9]+\b/g;
const REFERENCE_ID = /^[A-Z]{3}-A?\d{3}$/;
const RULES_FILE = "docs/parity.md";

/** The cells of the data rows of every table in `text` whose first header cell is `head`. */
function tableRows(text, head) {
  const rows = [];
  let inside = false;
  for (const line of text.split("\n")) {
    if (!line.startsWith("|")) {
      inside = false;
      continue;
    }
    const cells = line
      .split(/(?<!\\)\|/)
      .slice(1, -1)
      .map((cell) => cell.trim());
    if (/^\|[\s:|-]*-[\s:|-]*$/.test(line)) continue;
    if (cells[0] === head && !inside) inside = true;
    else if (inside) rows.push(cells);
  }
  return rows;
}

/** `pattern` with every `{a,b}` group expanded, innermost first; an alternative may be empty. */
function expandBraces(pattern) {
  const group = /\{([^{}]*)\}/.exec(pattern);
  if (!group) return [pattern];
  return group[1]
    .split(",")
    .flatMap((alt) =>
      expandBraces(
        pattern.slice(0, group.index) + alt + pattern.slice(group.index + group[0].length),
      ),
    );
}

/** Whether a path pattern (`{a,b}`, `*`, `**`) names an existing file or directory under a test root; every expansion must. */
function pathExists(token, exists) {
  const globs = expandBraces(token.replace(/\/$/, "")).map((glob) => {
    const body = glob
      .split("**")
      .map((part) => part.split("*").map(escapeRegex).join("[^/]*"))
      .join(".*");
    return new RegExp(`^${body}$`);
  });
  return globs.every((glob) =>
    TEST_ROOTS.some((root) =>
      [...exists].some((file) => file.startsWith(root) && glob.test(file.slice(root.length))),
    ),
  );
}

function escapeRegex(text) {
  return text.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
}

/** The problems in the parity files. `docs` maps a path to its text; `exists` holds every file and directory. */
function checkParity({ docs, exists, targets }) {
  const problems = [];
  const index = docs.get(RULES_FILE) ?? "";
  const areaFiles = [...docs.keys()].filter((name) => /^docs\/parity\/[^/]+\.md$/.test(name));
  const linked = new Set(
    linkTargets(index)
      .filter((target) => /^parity\/[\w-]+\.md$/.test(target))
      .map((target) => `docs/${target}`),
  );
  for (const name of linked)
    if (!exists.has(name))
      problems.push(`${RULES_FILE} links ${name}, which does not exist; fix the link.`);
  for (const name of areaFiles) {
    if (!linked.has(name))
      problems.push(`${name} is not linked from ${RULES_FILE}; add it to the table.`);
    const pair = name.endsWith("-deviations.md")
      ? name.replace("-deviations", "")
      : name.replace(/\.md$/, "-deviations.md");
    if (!docs.has(pair))
      problems.push(
        `${name} has no pair ${pair}; every area has a rows file and a deviations file.`,
      );
  }

  const owner = new Map(); // row ID -> rows file
  const priority = new Map();
  const droppedM = new Set(); // IDs of dropped M rows.
  for (const name of areaFiles.filter((file) => !file.endsWith("-deviations.md"))) {
    for (const cells of tableRows(docs.get(name), "ID")) {
      const [id, , p, status, test] = cells;
      if (!ROW_ID.test(id)) {
        problems.push(
          `${name}: a row starts with "${id}", which is not a row ID like WS-01; fix it.`,
        );
        continue;
      }
      if (cells.length !== 6) problems.push(`${name} ${id}: has ${cells.length} cells, want 6.`);
      if (!PRIORITIES.has(p)) problems.push(`${name} ${id}: P "${p}" is not M or S.`);
      if (!STATUSES.has(status))
        problems.push(
          `${name} ${id}: Status "${status}" is not todo, in progress, done or dropped.`,
        );
      if (owner.has(id))
        problems.push(`${name} ${id}: also a row of ${owner.get(id)}; row IDs are unique.`);
      owner.set(id, name);
      priority.set(id, p);
      if (status === "dropped" && p === "M") droppedM.add(id);
      const tokens = [...(test ?? "").matchAll(/`([^`]+)`/g)].map((m) => m[1]);
      if (status === "done" && !tokens.length)
        problems.push(`${name} ${id}: done, so Test must name a test.`);
      for (const token of tokens) {
        const make = /^make (\S+)$/.exec(token);
        if (make ? !targets.has(make[1]) : !pathExists(token, exists))
          problems.push(
            `${name} ${id}: Test \`${token}\` matches no file or Makefile target; spell the path from the repository root, crates/ or frontend/src/.`,
          );
      }
    }
  }

  const scope = new Map();
  for (const cells of tableRows(docs.get("docs/rebuild/01-product-scope.md") ?? "", "ID"))
    if (ROW_ID.test(cells[0])) scope.set(cells[0], cells[2]);
    else
      problems.push(
        `docs/rebuild/01-product-scope.md: a row starts with "${cells[0]}", which is not a row ID like WS-01; fix it.`,
      );
  for (const [id, p] of scope) {
    if (!owner.has(id))
      problems.push(
        `${id} is a row of docs/rebuild/01-product-scope.md but in no parity rows file; add it.`,
      );
    else if (priority.get(id) !== p)
      problems.push(
        `${owner.get(id)} ${id}: P ${priority.get(id)} differs from ${p} in 01; 01 decides.`,
      );
  }
  for (const [id, name] of owner)
    if (!scope.has(id))
      problems.push(
        `${name} ${id}: not a row of docs/rebuild/01-product-scope.md; add it there first.`,
      );

  for (const name of areaFiles.filter((file) => file.endsWith("-deviations.md"))) {
    const rowsFile = name.replace("-deviations", "");
    const named = new Set();
    for (const cells of tableRows(docs.get(name), "Row")) {
      if (cells.length !== 3) {
        problems.push(
          `${name}: a row has ${cells.length} cells, want 3 (Row, Deviation, Reason): ${cells[0]}.`,
        );
        continue;
      }
      const ids = cells[0].match(ID_LIKE) ?? [];
      for (const id of ids) named.add(id);
      if (!ids.length)
        problems.push(`${name} "${cells[0]}": names no row ID; name the row it deviates on.`);
      for (const id of ids.filter((id) => !REFERENCE_ID.test(id) && !owner.has(id)))
        problems.push(
          `${name} ${cells[0]}: ${id} is neither a parity row nor a reference ID like VPC-009; fix it.`,
        );
      const first = ids.find((id) => ROW_ID.test(id));
      if (first && owner.get(first) && owner.get(first) !== rowsFile)
        problems.push(
          `${name} ${cells[0]}: first row ${first} belongs to ${owner.get(first)}; move the deviation to its deviations file.`,
        );
    }
    for (const id of droppedM)
      if (owner.get(id) === rowsFile && !named.has(id))
        problems.push(
          `${rowsFile} ${id}: dropped M row; record the owner's written decision as a row in ${name}.`,
        );
  }
  return problems;
}

/** GitHub's heading slug of the rendered text: link destinations and emphasis marks dropped, code text kept. */
function slug(heading) {
  const plain = heading
    .split(/(`[^`]*`)/)
    .map((part, i) =>
      i % 2
        ? part
        : part
            .replace(/\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])/g, "$1")
            .replace(/\*+/g, "")
            .replace(/(^|[^\p{L}\p{N}])_+|_+(?=[^\p{L}\p{N}]|$)/gu, "$1"),
    )
    .join("");
  return plain
    .toLowerCase()
    .replace(/[^\p{L}\p{N} _-]/gu, "")
    .replace(/ /g, "-");
}

/** The text of `text` outside fenced code blocks; inline code spans are removed unless `keepSpans`. */
function withoutCode(text, keepSpans = false) {
  let fence = null; // The open fence's character and length. Any indentation: fences inside list items.
  return text
    .split("\n")
    .map((line) => {
      const mark = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
      if (fence) {
        if (mark && mark[1][0] === fence[0] && mark[1].length >= fence.length && !mark[2].trim())
          fence = null;
        return "";
      }
      if (mark) {
        fence = mark[1];
        return "";
      }
      return keepSpans ? line : line.replace(/`[^`]*`/g, "");
    })
    .join("\n");
}

/** The anchors (slugs, repeats numbered) of the headings in `text`. */
function anchors(text) {
  const seen = new Map();
  const found = new Set();
  for (const line of withoutCode(text, true).split("\n")) {
    const heading = /^#{1,6}\s+(.*?)\s*#*\s*$/.exec(line);
    if (!heading) continue;
    const base = slug(heading[1]);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    found.add(n ? `${base}-${n}` : base);
  }
  return found;
}

/** The destinations of the inline and reference-definition links in `text`, outside code, without `<>`. */
function linkTargets(text) {
  const code = withoutCode(text);
  const inline = code.matchAll(
    /!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\)/g,
  );
  const defined = code.matchAll(/^ {0,3}\[(?!\^)[^\]]+\]:\s*(<[^>]+>|\S+)/gm);
  return [...inline, ...defined].map((match) => match[1].replace(/^<|>$/g, ""));
}

/** The problems in relative markdown links across `docs` (every listed `*.md`), and how many links it checked. */
function checkLinks({ docs, exists }) {
  const problems = [];
  let links = 0;
  for (const [file, text] of docs) {
    for (const target of linkTargets(text)) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
      links++;
      const [rel, anchor] = target.split("#");
      const resolved = rel
        ? path.posix.normalize(path.posix.join(path.posix.dirname(file), rel))
        : file;
      if (!exists.has(resolved))
        problems.push(
          `${file}: link ${target} points at ${resolved}, which does not exist; fix or remove it.`,
        );
      else if (anchor && docs.has(resolved) && !anchors(docs.get(resolved)).has(anchor))
        problems.push(
          `${file}: link ${target} has no heading "#${anchor}" in ${resolved}; fix the anchor.`,
        );
    }
  }
  return { problems, links };
}

/** The repository's files (tracked or untracked, not ignored, present on disk) as `{ files, docs, targets }`. */
function load(root) {
  const listed = execFileSync("git", ["ls-files", "-co", "--exclude-standard"], {
    cwd: root,
    encoding: "utf8",
  })
    .split("\n")
    .filter((file) => file && fs.existsSync(path.join(root, file)));
  const exists = new Set(listed);
  for (const file of listed)
    for (let dir = path.posix.dirname(file); dir !== "."; dir = path.posix.dirname(dir))
      exists.add(dir);
  const docs = new Map(
    listed
      .filter((file) => file.endsWith(".md"))
      .map((file) => [file, fs.readFileSync(path.join(root, file), "utf8")]),
  );
  const makefile = fs.readFileSync(path.join(root, "Makefile"), "utf8");
  const targets = new Set([...makefile.matchAll(/^([\w.-]+):/gm)].map((m) => m[1]));
  return { docs, exists, targets };
}

module.exports = { tableRows, expandBraces, pathExists, slug, anchors, checkParity, checkLinks };

if (require.main === module) {
  const ctx = load(path.join(__dirname, ".."));
  const { problems: linkProblems, links } = checkLinks(ctx);
  const problems = [...checkParity(ctx), ...linkProblems];
  if (problems.length) {
    console.error(`Docs check failed:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  const rows = [...ctx.docs]
    .filter(([name]) => /^docs\/parity\/(?!.*-deviations)[^/]+\.md$/.test(name))
    .reduce(
      (n, [, text]) => n + tableRows(text, "ID").filter((cells) => ROW_ID.test(cells[0])).length,
      0,
    );
  console.log(`Docs check passed: ${rows} parity rows, ${links} links.`);
}
