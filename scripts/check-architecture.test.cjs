// Self-check of scripts/check-architecture.cjs against small synthetic workspaces. Run with
// `node --test scripts/check-architecture.test.cjs`.
const assert = require("node:assert/strict");
const test = require("node:test");
const { check, checkRoutes, checkProjection } = require("./check-architecture.cjs");

/** A workspace of `{ name, layer, deps }` crates in the shape of `cargo metadata`. */
function workspace(crates) {
  const packages = crates.map(({ name, layer, deps = [] }) => ({
    id: name,
    name,
    manifest_path: `${name}/Cargo.toml`,
    metadata: { "ax-devil": { layer } },
    dependencies: deps.map((dep) => (typeof dep === "string" ? { name: dep, kind: null } : dep)),
  }));
  return { workspace_members: packages.map((pkg) => pkg.id), packages };
}

const problemsOf = (crates) => check(workspace(crates)).problems;

void test("a clean workspace passes", () => {
  assert.deepEqual(
    problemsOf([
      { name: "foundation", layer: "foundation" },
      { name: "media", layer: "services", deps: ["foundation", "ffmpeg-next"] },
      { name: "playback", layer: "services", deps: ["foundation"] },
    ]),
    [],
  );
});

void test("playback may not depend on media, even as a sibling in the same layer", () => {
  const problems = problemsOf([
    { name: "media", layer: "services" },
    { name: "playback", layer: "services", deps: ["media"] },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /playback depends on media/);
});

void test("the drawing contract may not depend on scene", () => {
  const problems = problemsOf([
    { name: "scene", layer: "domain" },
    { name: "drawing", layer: "domain", deps: ["scene"] },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /drawing depends on scene/);
});

void test("only media may depend on ffmpeg-next", () => {
  const problems = problemsOf([
    { name: "media", layer: "services", deps: ["ffmpeg-next"] },
    { name: "export", layer: "services", deps: ["ffmpeg-next"] },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /export depends on ffmpeg-next/);
});

void test("layers 1-3 may derive serde and ts-rs but stay free of the HTTP server", () => {
  assert.deepEqual(
    problemsOf([{ name: "sessions", layer: "services", deps: ["serde", "ts-rs"] }]),
    [],
  );
  const problems = problemsOf([{ name: "sessions", layer: "services", deps: ["axum"] }]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /sessions \(services\) depends on axum/);
});

void test("a dev-dependency is exempt and a higher layer is still rejected", () => {
  assert.deepEqual(
    problemsOf([
      { name: "media", layer: "services" },
      { name: "playback", layer: "services", deps: [{ name: "media", kind: "dev" }] },
    ]),
    [],
  );
  const problems = problemsOf([
    { name: "api-types", layer: "adapters" },
    { name: "playback", layer: "services", deps: ["api-types"] },
  ]);
  assert.match(problems[0], /a higher layer/);
});

void test("a route is mounted only in the route table", () => {
  const mount = 'Router::new().route("/api/x", get(x))';
  assert.deepEqual(
    checkRoutes([
      { file: "src/routes/mod.rs", text: mount },
      { file: "src/lib.rs", text: "// a comment mentioning .route( is fine\nfn app() {}" },
    ]),
    [],
  );
  const problems = checkRoutes([
    { file: "src/session/socket.rs", text: `fn r() {\n  ${mount}\n}` },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /src\/session\/socket\.rs mounts a route/);
});

void test("the projection names every field of Doc: no rest pattern", () => {
  const file = "src/session/project.rs";
  const clean =
    "// `Doc { .. }` in a comment is fine\nlet Doc { workspace } = doc;\nlet n = x..=y;";
  assert.deepEqual(checkProjection([{ file, text: clean }]), []);
  for (const rest of [
    "let Doc { workspace, .. } = doc;",
    "let Doc {\n  workspace,\n  ..\n} = doc;",
  ]) {
    const problems = checkProjection([{ file, text: rest }]);
    assert.equal(problems.length, 1, rest);
    assert.match(problems[0], /rest pattern/);
  }
  assert.match(checkProjection([])[0], /is missing/);
});
