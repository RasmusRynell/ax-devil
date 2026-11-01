// Enforces the layer rules of docs/architecture/overview.md, and the crate-level rules below, on the Cargo workspace.
// Reads `cargo metadata --format-version 1` on stdin; every workspace crate declares its layer in
// `[package.metadata.ax-devil] layer = "..."`.
const fs = require("node:fs");

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
    from: "drawing",
    to: "scene",
    why: "the drawing contract never knows Scenes",
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

module.exports = { check };

if (require.main === module) {
  const { problems, crates } = check(JSON.parse(fs.readFileSync(0, "utf8")));
  if (problems.length) {
    console.error(`Architecture check failed:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`Architecture check passed for ${crates} crates.`);
}
