// Enforces the layer rules of docs/architecture/overview.md, and the crate-level rules below, on the Cargo workspace.
// Reads `cargo metadata --format-version 1` on stdin; every workspace crate declares its layer in
// `[package.metadata.ax-devil] layer = "..."`.
const fs = require("node:fs");
const path = require("node:path");

// Lowest first. A crate may depend on workspace crates in its own layer or below.
const LAYERS = ["foundation", "domain", "services", "adapters", "entry"];
// Layers 1-3 stay free of the HTTP/WebSocket server and anything else that ties them to how the UI is reached.
const TOOLKIT_FREE = new Set(["foundation", "domain", "services"]);
const SERVER_CRATES = new Set([
  "axum",
  "axum-core",
  "hyper",
  "tower",
  "tower-http",
  "rust-embed",
  "ts-rs",
]);

// Rules the layers alone do not say. `from` is a crate name, or "*" for every crate except those in `except`; `to` is
// a dependency, workspace or not. Dev-dependencies are exempt, like in the layer rule.
const FORBIDDEN = [
  {
    from: "playback",
    to: "media",
    why: "playback knows frames and windows, not FFmpeg; the root crate joins them (docs/architecture/module-map.md)",
  },
  {
    from: "*",
    except: ["media"],
    to: "ffmpeg-next",
    why: "only the media crate binds FFmpeg",
  },
  {
    from: "foundation",
    to: "tokio",
    why: "foundation has no I/O and no async runtime",
  },
  {
    from: "media",
    to: "api-types",
    why: "wire types belong to the adapter layer",
  },
];

/** The problems in `metadata` (the parsed `cargo metadata` output), one sentence each. */
function check(metadata) {
  const members = new Set(metadata.workspace_members);
  const workspace = metadata.packages.filter((pkg) => members.has(pkg.id));
  const layerOf = new Map(workspace.map((pkg) => [pkg.name, pkg.metadata?.["ax-devil"]?.layer]));
  const problems = [];

  for (const pkg of workspace) {
    const layer = layerOf.get(pkg.name);
    if (!LAYERS.includes(layer)) {
      problems.push(
        `${pkg.manifest_path}: set [package.metadata.ax-devil] layer to one of ${LAYERS.join(", ")}`,
      );
      continue;
    }
    for (const dependency of pkg.dependencies) {
      if (dependency.kind === "dev") continue;
      const target = layerOf.get(dependency.name);
      if (target !== undefined && LAYERS.indexOf(target) > LAYERS.indexOf(layer))
        problems.push(
          `${pkg.name} (${layer}) depends on ${dependency.name} (${target}), a higher layer`,
        );
      if (TOOLKIT_FREE.has(layer) && SERVER_CRATES.has(dependency.name))
        problems.push(
          `${pkg.name} (${layer}) depends on ${dependency.name}; layers 1-3 stay server-free`,
        );
      for (const rule of FORBIDDEN) {
        const applies =
          rule.to === dependency.name &&
          (rule.from === pkg.name ||
            (rule.from === "*" && !(rule.except ?? []).includes(pkg.name)));
        if (applies) problems.push(`${pkg.name} depends on ${dependency.name}: ${rule.why}`);
      }
    }
  }
  return { problems, crates: workspace.length };
}

// The only file that may mount a route: the route table.
const ROUTE_TABLE = "src/routes/mod.rs";

/** The problems in `sources` (`{ file, text }` Rust files, `file` relative to the repository), one sentence each. */
function checkRoutes(sources) {
  return sources
    .filter(({ file }) => file !== ROUTE_TABLE)
    .filter(({ text }) => text.split("\n").some((line) => /\.route\(/.test(line.split("//")[0])))
    .map(
      ({ file }) => `${file} mounts a route; add the route to the table in ${ROUTE_TABLE} instead`,
    );
}

// The only file that builds the Doc's wire form. It destructures `Doc` exhaustively, so a new Doc field does not
// compile until the projection decides what each audience sees of it.
const PROJECTION = "src/session/project.rs";

/** The problems in `sources` (as for `checkRoutes`) with the projection: it must exist and use no rest pattern. */
function checkProjection(sources) {
  const projection = sources.find(({ file }) => file === PROJECTION);
  if (!projection) return [`${PROJECTION} is missing; it is the only constructor of DocWire`];
  const code = projection.text
    .split("\n")
    .map((line) => line.split("//")[0])
    .join("\n");
  // `..` closing a pattern: `, ..`, `..` before `}`.
  return /\.\.\s*\}|,\s*\.\.(?![.=\w])/.test(code)
    ? [
        `${PROJECTION} uses a rest pattern (\`..\`); name every field of Doc so a new one must be projected`,
      ]
    : [];
}

/** Every Rust file under `src` and under each crate's `src` in the repository at `root`. */
function rustSources(root) {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
      const file = `${dir}/${entry.name}`;
      if (entry.isDirectory()) walk(file);
      else if (entry.name.endsWith(".rs"))
        found.push({ file, text: fs.readFileSync(path.join(root, file), "utf8") });
    }
  };
  walk("src");
  for (const crate of fs.readdirSync(path.join(root, "crates"))) walk(`crates/${crate}/src`);
  return found;
}

module.exports = { check, checkRoutes, checkProjection };

if (require.main === module) {
  const { problems, crates } = check(JSON.parse(fs.readFileSync(0, "utf8")));
  const sources = rustSources(path.join(__dirname, ".."));
  problems.push(...checkRoutes(sources), ...checkProjection(sources));
  if (problems.length) {
    console.error(`Architecture check failed:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`Architecture check passed for ${crates} crates.`);
}
