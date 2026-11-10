// Enforces the layer rules of docs/architecture/overview.md, and the crate-level rules below, on the Cargo workspace,
// and the import layers on frontend/src (check-frontend-layers.cjs).
// Reads `cargo metadata --format-version 1` on stdin; every workspace crate declares its layer in
// `[package.metadata.ax-devil] layer = "..."`.
const fs = require("node:fs");
const path = require("node:path");
const { checkFrontendLayers, FRONTEND, SOURCE_FILE } = require("./check-frontend-layers.cjs");

// Lowest first. A crate may depend on workspace crates in its own layer or below.
const LAYERS = ["foundation", "domain", "services", "adapters", "entry"];
// Layers 1-3 stay free of the HTTP/WebSocket server and anything else that ties them to how the UI is reached. `serde`
// and `ts-rs` are not in this set: they only describe plain data, so a type is defined once at the layer that owns it
// and carries its own wire form.
const TOOLKIT_FREE = new Set(["foundation", "domain", "services"]);
const SERVER_CRATES = new Set(["axum", "axum-core", "hyper", "tower", "tower-http", "rust-embed"]);

// Rules the layers alone do not say. `from` is a crate name, or "*" for every crate except those in `except`; `to` is
// a dependency, workspace or not, or `{ anyWorkspaceCrate: true }` for every workspace crate. Dev-dependencies are
// exempt, like in the layer rule.
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
    from: "camera",
    to: { anyWorkspaceCrate: true },
    why: "camera is usable and testable without the app, so it depends on no workspace crate",
  },
  ...["retina", "rumqttc", "reqwest", "http-auth", "tokio-tungstenite"].map((to) => ({
    from: "*",
    except: ["camera"],
    to,
    why: "only the camera crate talks to cameras and brokers",
  })),
  {
    from: "*",
    except: ["ax-devil"],
    to: "camera",
    why: "only the root crate joins the camera crate to the app",
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
    // The Makefile's second test and clippy pass (`-p ax-devil --no-default-features`) covers every crate only while
    // no other one has default features.
    if (pkg.name !== "ax-devil" && "default" in (pkg.features ?? {}))
      problems.push(
        `${pkg.manifest_path}: declares a default feature; the Makefile's test-backend and _clippy-no-default run --no-default-features for ax-devil and camera only; extend them first`,
      );
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
        const named =
          rule.to === dependency.name ||
          (rule.to.anyWorkspaceCrate && layerOf.has(dependency.name));
        const applies =
          named &&
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

/** Every file under `dir` (relative to the repository at `root`) whose name matches `pattern`, as `{ file, text }`. */
function filesUnder(root, dir, pattern) {
  const found = [];
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const file = `${dir}/${entry.name}`;
    if (entry.isDirectory()) found.push(...filesUnder(root, file, pattern));
    else if (pattern.test(entry.name))
      found.push({ file, text: fs.readFileSync(path.join(root, file), "utf8") });
  }
  return found;
}

/** Every Rust file under `src` and under each crate's `src` in the repository at `root`. */
function rustSources(root) {
  const crates = fs.readdirSync(path.join(root, "crates"));
  return [
    ...filesUnder(root, "src", /\.rs$/),
    ...crates.flatMap((crate) => filesUnder(root, `crates/${crate}/src`, /\.rs$/)),
  ];
}

module.exports = { check, checkRoutes, checkProjection, checkFrontendLayers };

if (require.main === module) {
  const root = path.join(__dirname, "..");
  const { problems, crates } = check(JSON.parse(fs.readFileSync(0, "utf8")));
  const sources = rustSources(root);
  const frontend = filesUnder(root, FRONTEND, SOURCE_FILE);
  problems.push(...checkRoutes(sources), ...checkProjection(sources));
  if (frontend.length === 0) problems.push(`${FRONTEND} has no source files to check`);
  try {
    problems.push(...checkFrontendLayers(frontend));
  } catch (error) {
    problems.push(error.message);
  }
  if (problems.length) {
    console.error(`Architecture check failed:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log(
    `Architecture check passed for ${crates} crates and ${frontend.length} frontend files.`,
  );
}
