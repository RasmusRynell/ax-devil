// Self-check of scripts/check-architecture.cjs against small synthetic workspaces. Run with
// `node --test scripts/check-architecture.test.cjs`.
const assert = require("node:assert/strict");
const test = require("node:test");
const {
  check,
  checkRoutes,
  checkProjection,
  checkFrontendLayers,
} = require("./check-architecture.cjs");

/** A workspace of `{ name, layer, deps, features }` crates in the shape of `cargo metadata`. */
function workspace(crates) {
  const packages = crates.map(({ name, layer, deps = [], features }) => ({
    id: name,
    features,
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

void test("camera depends on no workspace crate, and only camera may use the camera-side clients", () => {
  assert.deepEqual(
    problemsOf([
      {
        name: "camera",
        layer: "services",
        deps: ["reqwest", "http-auth", "retina", "rumqttc", "tokio-tungstenite"],
      },
    ]),
    [],
  );
  const problems = problemsOf([
    { name: "foundation", layer: "foundation" },
    { name: "camera", layer: "services", deps: ["foundation"] },
    { name: "sessions", layer: "services", deps: ["reqwest", "rumqttc", "tokio-tungstenite"] },
  ]);
  assert.equal(problems.length, 4);
  assert.match(problems[0], /camera depends on foundation/);
  assert.match(problems[1], /sessions depends on reqwest/);
  assert.match(problems[2], /sessions depends on rumqttc/);
  assert.match(problems[3], /sessions depends on tokio-tungstenite/);
});

void test("only the root crate may depend on camera", () => {
  assert.deepEqual(
    problemsOf([
      { name: "camera", layer: "services" },
      { name: "ax-devil", layer: "entry", deps: ["camera"] },
    ]),
    [],
  );
  const problems = problemsOf([
    { name: "camera", layer: "services" },
    { name: "sessions", layer: "services", deps: ["camera"] },
  ]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /sessions depends on camera/);
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

void test("only the root crate may have default features", () => {
  assert.deepEqual(
    problemsOf([{ name: "ax-devil", layer: "entry", features: { default: ["embed-assets"] } }]),
    [],
  );
  const problems = problemsOf([{ name: "media", layer: "services", features: { default: [] } }]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /media\/Cargo\.toml: declares a default feature/);
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

void test("a frontend file may not import a higher layer, in any import form", () => {
  // `[text, line of the specifier]`, after a first line of comment.
  const forms = [
    ['import { A } from "../shell/x";', 2],
    ['import type { A } from "../shell/x";', 2],
    ['import {\n  A,\n  B,\n} from "../shell/x";', 5],
    ['import { A } from /* why */ "../shell/x";', 2],
    ['export * from "../shell/x";', 2],
    ['export { A } from "../shell/x";', 2],
    ['import "../shell/x";', 2],
    ['import A = require("../shell/x");', 2],
    ['const m = await import("../shell/x");', 2],
    ["void import(`../shell/x`);", 2],
    ['const m = require("../shell/x");', 2],
    ['type T = typeof import("../shell/x");', 2],
    ['vi.mock("../shell/x", () => ({}));', 2],
    ['vi.mock(import("../shell/x"));', 2],
    ['new URL("../shell/x", import.meta.url);', 2],
    ['import { W } from "../shell/x.js?raw";', 2],
  ];
  const sources = forms.map(([text], i) => ({
    file: `frontend/src/kit/f${i}.tsx`,
    text: `// top\n${text}`,
  }));
  sources.push(
    { file: "frontend/src/kit/g.ts", text: 'import type { B } from "../client/windowBridge.js";' },
    {
      file: "frontend/src/kit/h.css",
      text: '/* @import "../shell/x.css"; */\n@import url(../shell/x.css);',
    },
    // A schemeless name is relative when a scanned file has it, else a package.
    { file: "frontend/src/tokens.css", text: '@import "tailwindcss";\n@import "shell/x";' },
    { file: "frontend/src/shell/x.css", text: "" },
    { file: "frontend/src/shell/Shell.tsx", text: "" },
    { file: "frontend/src/shell/x/y.tsx", text: "" },
    {
      file: "frontend/src/kit/i.css",
      text: '@IMPORT "../shell/x.css";\n@import /* shared */ URL(../shell/x.css);',
    },
  );
  // What the check cannot follow is itself a problem: `[file, text, start of the problem]`.
  const unfollowed = [
    [
      "kit/r0.ts",
      'import.meta.glob("./x/*.ts");',
      "frontend/src/kit/r0.ts:1 uses import.meta.glob",
    ],
    [
      "kit/r1.ts",
      "void import(`../shell/${name}.tsx?raw`);",
      "frontend/src/kit/r1.ts:1 imports a computed path",
    ],
    [
      "kit/r6.ts",
      "new URL(`../../${name}`, import.meta.url);",
      "frontend/src/kit/r6.ts:1 imports a computed path",
    ],
    [
      "kit/r7.ts",
      "new URL(`../../../../${name}`, import.meta.url);",
      "frontend/src/kit/r7.ts:1 imports a computed path",
    ],
    [
      "kit/r8.ts",
      '/// <reference path="../shell/types.d.ts" />',
      "frontend/src/kit/r8.ts:1 imports frontend/src/shell/types, but kit is below shell;",
    ],
    [
      "kit/r9.ts",
      'declare module "../shell/x" {}',
      "frontend/src/kit/r9.ts:1 imports frontend/src/shell/x, but kit is below shell;",
    ],
    [
      "kit/r10.ts",
      "import.meta.resolve(somePath);",
      "frontend/src/kit/r10.ts:1 imports a computed path",
    ],
    [
      "kit/r11.ts",
      'import.meta["glob"]("./x");',
      "frontend/src/kit/r11.ts:1 uses import.meta.glob",
    ],
    [
      "kit/r12.css",
      '@import "..\\/shell/x.css";',
      "frontend/src/kit/r12.css:1 has an @import the layer check cannot read",
    ],
    [
      "kit/r13.ts",
      'import "/src/shell/x.ts";',
      "frontend/src/kit/r13.ts:1 imports by an absolute path",
    ],
    [
      "kit/r14.ts",
      '/// <reference path="nested/../../shell/x.d.ts" />',
      "frontend/src/kit/r14.ts:1 imports frontend/src/shell/x, but kit is below shell;",
    ],
    [
      "kit/r15.ts",
      '/// <reference path="..\\shell\\x.d.ts" />',
      "frontend/src/kit/r15.ts:1 has an import path with a backslash",
    ],
    [
      "kit/r16.ts",
      'import "/@fs/home/me/checkout/frontend/src/shell/x.ts?url";',
      "frontend/src/kit/r16.ts:1 imports by an absolute path",
    ],
    [
      "kit/r17.ts",
      "void import(`/@fs/${root}/x.ts`);",
      "frontend/src/kit/r17.ts:1 imports by an absolute path",
    ],
    ...[
      ["r18", 'import "file:///abs/checkout/frontend/src/shell/x.ts?url";'],
      ["r19", 'import "/abs/checkout/frontend/src/shell/x.ts";'],
      ["r20", 'import "C:/x/frontend/src/shell/x.ts";'],
      ["r21", 'import "/public/x.svg";'],
    ].map(([name, text]) => [
      `kit/${name}.ts`,
      text,
      `frontend/src/kit/${name}.ts:1 imports by an absolute path`,
    ]),
    [
      "kit/r22.ts",
      'import "typescript/../../src/shell/Shell.tsx";',
      "frontend/src/kit/r22.ts:1 imports a package path with a",
    ],
    [
      "kit/r23.ts",
      'import "./x%2e%2e/y";',
      "frontend/src/kit/r23.ts:1 has an import path with a backslash or percent-escape",
    ],
    // Any scheme but http(s), data, node and virtual is a problem; a one-letter one reads as a drive letter.
    ...[
      ["x:%20/../../tokens.css", "has an import path with a backslash or percent-escape"],
      ["https:%20/../../tokens.css", "imports through an unrecognized URL scheme"],
      ["x://../../tokens.css", "imports by an absolute path"],
      ["git+https://../../tokens.css", "imports through an unrecognized URL scheme"],
    ].map(([spec, problem], i) => [
      `generated/p${i}.css`,
      `@import "${spec}";`,
      `frontend/src/generated/p${i}.css:1 ${problem}`,
    ]),
    ["kit/r2.ts", "void import(somePath);", "frontend/src/kit/r2.ts:1 imports a computed path"],
    ["kit/r3.ts", "vi.mock(somePath);", "frontend/src/kit/r3.ts:1 imports a computed path"],
    [
      "kit/r4.ts",
      "new URL(somePath, import.meta.url);",
      "frontend/src/kit/r4.ts:1 imports a computed path",
    ],
    [
      "kit/r5.css",
      "@import garbage;",
      "frontend/src/kit/r5.css:1 has an @import the layer check cannot read",
    ],
  ];
  unfollowed.forEach(([file, text]) => sources.push({ file: `frontend/src/${file}`, text }));
  const problems = checkFrontendLayers(sources);
  assert.equal(problems.length, forms.length + 3 + 2 + unfollowed.length);
  unfollowed.forEach(([, , start]) => {
    assert.ok(
      problems.some((p) => p.startsWith(start)),
      start,
    );
  });
  assert.ok(
    problems.some((p) => p.startsWith("frontend/src/kit/i.css:1 imports frontend/src/shell/x")),
  );
  assert.ok(
    problems.some((p) => p.startsWith("frontend/src/kit/i.css:2 imports frontend/src/shell/x")),
  );
  forms.forEach(([, line], i) => {
    const prefix = `frontend/src/kit/f${i}.tsx:${line} imports frontend/src/shell/x`;
    assert.ok(problems[i].startsWith(prefix), problems[i]);
  });
  assert.ok(
    problems[forms.length].startsWith(
      "frontend/src/kit/g.ts:1 imports frontend/src/client/windowBridge, but kit is below client;",
    ),
  );
  assert.ok(
    problems[forms.length + 1].startsWith(
      "frontend/src/kit/h.css:2 imports frontend/src/shell/x, but kit is below shell;",
    ),
  );
  assert.ok(
    problems[forms.length + 2].startsWith(
      "frontend/src/tokens.css:2 imports frontend/src/shell/x, but kit is below shell;",
    ),
  );
  assert.match(problems[0], /but kit is below shell; move the shared code to kit or lower/);
});

void test("same-layer, lower-layer, package, asset, comment and string imports pass; unlisted units fail", () => {
  const ok = [
    {
      file: "frontend/src/shell/a.tsx",
      text: 'import { b } from "../kit/b";\nimport "./c";\nimport "react";',
    },
    { file: "frontend/src/kit/a.tsx", text: 'import "./b";\nimport "../../../assets/logo.svg";' },
    {
      file: "frontend/src/kit/b.tsx",
      text: '// import { S } from "../shell/Shell";\n/* import("../shell/x") */\nconst e = `import { S } from "../shell/Shell";`;\nconst f = \'import "../shell/x"\';',
    },
    // Calls that take no module path, and CSS: a string is not an import, a fragment is not a unit.
    {
      file: "frontend/src/kit/nested/e.ts",
      text: "new URL(`../../../../fixtures/wire/${name}`, import.meta.url);",
    },
    {
      file: "frontend/src/kit/o.ts",
      text: '/// <reference path="./other.d.ts" />\nimport "//cdn.example/x.js";\nimport "https://example.com/x.js";\nvoid import("https://x.example/a%20b.js");\nnew URL("https://x.example/a%20b", import.meta.url);',
    },
    {
      file: "frontend/src/kit/n.css",
      text: '@import "//cdn.example/x.css";\n@import url(https://example.com/x.css);\n@import "https://fonts.example/css2?family=Open%20Sans";\n@import "//cdn.example/theme%20dark.css";\n@import "node:fs";\n@import "virtual:thing";\n@import "data:text/css,a%20b";\n@import \'data:text/css,body::after{content:"a/../b"}\';\n@import "node:fs/promises";',
    },
    {
      file: "frontend/src/kit/d.ts",
      text: 'const a = vi.fn(someVar);\nvi.spyOn(obj, "x");\nconst u = new URL("./x", base);',
    },
    {
      file: "frontend/src/kit/c.css",
      text: 'a::after { content: \'@import "../shell/x.css";\'; }\n@import "../tokens.css#theme";\nb::after { content: "a\\\n@import \'../shell/x.css\';"; }',
    },
    { file: "frontend/src/App.test.tsx", text: 'import { a } from "./shell/a";' },
    {
      file: "frontend/src/style.css",
      text: '@import "tailwindcss";\n@import "@scope/pkg/sub.css";\n@import "./tokens.css";',
    },
  ];
  assert.deepEqual(checkFrontendLayers(ok), []);
  const problems = checkFrontendLayers([
    { file: "frontend/src/foo/x.ts", text: "" },
    { file: "frontend/src/foo/y.ts", text: "" },
    { file: "frontend/src/kit/z.ts", text: 'import data from "../data.json";' },
  ]);
  assert.deepEqual(problems, [
    "frontend/src/foo/x.ts: add foo to a layer in FRONTEND_LAYERS (scripts/check-frontend-layers.cjs)",
    "frontend/src/kit/z.ts:1 imports frontend/src/data.json; add data.json to a layer in FRONTEND_LAYERS (scripts/check-frontend-layers.cjs)",
  ]);
});
