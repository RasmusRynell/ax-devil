// Self-check of scripts/check-architecture.cjs against small synthetic workspaces. Run with
// `node --test scripts/check-architecture.test.cjs`.
const assert = require("node:assert/strict");
const test = require("node:test");
const { check } = require("./check-architecture.cjs");

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

void test("only media may depend on ffmpeg-next", () => {
  const problems = problemsOf([
    { name: "media", layer: "services", deps: ["ffmpeg-next"] },
    { name: "export", layer: "services", deps: ["ffmpeg-next"] },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /export depends on ffmpeg-next/);
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
