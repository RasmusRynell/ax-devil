// The import-layer rule for frontend/src, used by check-architecture.cjs: every folder and root-level file ("unit") sits
// in one layer, and a file imports only its own layer or below. Literal paths are followed from the syntax tree
// (TypeScript compiler API, a frontend devDependency): import and export declarations, `import x = require`, import
// types, `declare module "x"`, `/// <reference path>`, `import()`, `import.meta.resolve`, `require`, the `vi` module-path
// methods, `new URL(x, import.meta.url)` and CSS `@import`. Only relative paths and package names are followed (a bare
// CSS name whose first segment is local is relative); `//`, http(s), data, node and virtual specifiers are not paths. These
// are problems themselves: an absolute path of any kind (`/x`, `file:`, a drive letter), any other URL scheme, a package path with a
// `.` or `..` segment, a backslash or percent-escape, `import.meta.glob`, a computed path that may reach frontend/src, and a
// CSS `@import` the scanner cannot read.
const path = require("node:path");

// Lowest first. A unit is a folder of frontend/src or a root-level file's name without extension.
const FRONTEND_LAYERS = {
  generated: ["generated"],
  kit: ["kit", "style", "tokens"],
  engines: ["present", "overlay", "search", "casefold"],
  client: [
    "access",
    "api",
    "errors",
    "settings",
    "secret",
    "deviceSettings",
    "fakeSettings",
    "fakeFullscreen",
    "commands",
    "recents",
    "resource",
    "windowBridge",
    "pickPath",
    "session",
  ],
  workflows: [
    "playback",
    "viewer",
    "viewers",
    "tools",
    "live",
    "export",
    "bookmarks",
    "resolvers",
    "plugins",
  ],
  shell: ["shell"],
  entry: ["App", "main", "render", "bench", "cli", "test-setup", "vite-env"],
};
const FRONTEND = "frontend/src";
const FRONTEND_LAYER_NAMES = Object.keys(FRONTEND_LAYERS);
const SOURCE_EXTENSION = /(\.d)?(\.test)?\.([cm]?[jt]sx?|css)$/;
/** The files of frontend/src the rule reads: every script and stylesheet. */
const SOURCE_FILE = /\.([cm]?[jt]sx?|css)$/;

let typescript;
/** The TypeScript compiler API from the frontend's dependencies; throws a one-line reason when it is not installed. */
function loadTypescript() {
  if (typescript) return typescript;
  try {
    const paths = [path.join(__dirname, "..", "frontend")];
    typescript = require(require.resolve("typescript", { paths }));
  } catch (error) {
    throw new Error(
      `frontend import check cannot load typescript (${error.code ?? error.message}); run pnpm install`,
    );
  }
  return typescript;
}

const COMPUTED =
  "imports a computed path, which the layer check cannot follow; write the path as a literal";
const GLOB =
  "uses import.meta.glob, which the layer check cannot follow; import each module by its path";
const BACKSLASH =
  "has an import path with a backslash or percent-escape, which the layer check cannot read; use forward slashes and no escapes";
const PACKAGE_PATH =
  'imports a package path with a ".." segment, which the layer check cannot follow; import the module by its relative path';
const UNKNOWN_SCHEME =
  "imports through an unrecognized URL scheme, which the layer check cannot follow; import the module by its relative path";
const ABSOLUTE =
  "imports by an absolute path, which the layer check cannot follow; import the module by its relative path";
/** Whether `spec` is no file load: `//host/…`, `http(s)://…`, or a `data:` payload (no path rule applies to it). */
const isIgnored = (spec) => spec.startsWith("//") || /^(https?:\/\/|data:)/i.test(spec);
/** Whether `spec` is a `node:` or `virtual:` specifier, which only the dot-segment and backslash rules apply to. */
const isNodeOrVirtual = (spec) => /^(node|virtual):/i.test(spec);
/** Whether `spec` has a scheme (two or more characters, so not a drive letter) the check does not know, `file:` aside. */
const isUnknownScheme = (spec) =>
  /^[a-z][a-z0-9+.-]+:/i.test(spec) && !/^file:/i.test(spec) && !isNodeOrVirtual(spec);
/** Whether the specifier `spec`, not relative or absolute, has a `.` or `..` path segment (a scheme-like one too: a loader may still resolve it as a path). */
const hasDotSegment = (spec) =>
  !/^(\.\.?\/|\/)/.test(spec) &&
  spec
    .split(/[?#]/)[0]
    .split("/")
    .some((segment) => segment === "." || segment === "..");
/** Whether `spec` names a file absolutely: a single leading `/` (also Vite's `/@fs/`), `file:`, or a drive letter. */
const isAbsolute = (spec) => /^(\/(?!\/)|file:|[a-z]:[\\/])/i.test(spec);
const UNREADABLE = "has an @import the layer check cannot read";
// The vi methods whose first argument is a module path.
const MODULE_PATH_METHODS = new Set([
  "mock",
  "doMock",
  "unmock",
  "doUnmock",
  "importActual",
  "importMock",
]);

/**
 * `{ line, spec }` for every module specifier in the script `text` of `file`, and `{ line, problem }` for every use the
 * check cannot follow: `import.meta.glob`, and a module path that is not a literal.
 */
function scriptImports(file, text) {
  const ts = loadTypescript();
  const kind = /\.[jt]sx$/.test(file)
    ? ts.ScriptKind.TSX
    : /\.[cm]?js$/.test(file)
      ? ts.ScriptKind.JS
      : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, false, kind);
  const found = [];
  const lineOf = (node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
  const isImportMeta = (node) =>
    ts.isMetaProperty(node) && node.keywordToken === ts.SyntaxKind.ImportKeyword;
  /**
   * Whether the template `node` is a computed path that cannot reach frontend/src: its literal head, cut back to its last
   * `/`, is a directory outside frontend/src that is not an ancestor of it. (A substitution that climbs back in with
   * `..` is out of scope.)
   */
  const staysOutside = (node) => {
    if (!ts.isTemplateExpression(node)) return false;
    const head = node.head.text.slice(0, node.head.text.lastIndexOf("/") + 1);
    if (!/^\.\.?\//.test(head)) return false;
    const dir = path.posix
      .normalize(path.posix.join(path.posix.dirname(file), head))
      .replace(/\/$/, "");
    // A directory above the repository is an ancestor too.
    if (dir === "" || dir === "." || dir === ".." || dir.startsWith("../")) return false;
    const inside = dir === FRONTEND || dir.startsWith(`${FRONTEND}/`);
    const ancestor = FRONTEND.startsWith(`${dir}/`);
    return !inside && !ancestor;
  };
  /** Records the module path `node`: its literal text, or a problem when it is anything else. */
  const add = (node) => {
    if (!node) return;
    if (ts.isTemplateExpression(node) && isIgnored(node.head.text)) return;
    if (ts.isTemplateExpression(node) && isAbsolute(node.head.text))
      found.push({ line: lineOf(node), problem: ABSOLUTE });
    else if (ts.isTemplateExpression(node) && isUnknownScheme(node.head.text))
      found.push({ line: lineOf(node), problem: UNKNOWN_SCHEME });
    else if (ts.isTemplateExpression(node) && hasDotSegment(node.head.text))
      found.push({ line: lineOf(node), problem: PACKAGE_PATH });
    else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
      found.push({ line: lineOf(node), spec: node.text });
    // `vi.mock(import("x"))` is covered by the inner import call.
    else if (
      !staysOutside(node) &&
      !(ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword)
    )
      found.push({ line: lineOf(node), problem: COMPUTED });
  };
  const isGlobAccess = (node) =>
    (ts.isPropertyAccessExpression(node) && node.name.text.startsWith("glob")) ||
    (ts.isElementAccessExpression(node) &&
      ts.isStringLiteralLike(node.argumentExpression) &&
      node.argumentExpression.text.startsWith("glob"));
  for (const reference of source.referencedFiles)
    found.push({
      line: source.getLineAndCharacterOfPosition(reference.pos).line + 1,
      spec: reference.fileName,
      reference: true,
    });
  const visit = (node) => {
    if (ts.isModuleDeclaration(node) && ts.isStringLiteral(node.name)) add(node.name);
    else if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      add(node.moduleSpecifier);
    else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference)
    )
      add(node.moduleReference.expression);
    else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument))
      add(node.argument.literal);
    else if (ts.isCallExpression(node)) {
      const callee = node.expression;
      const [first] = node.arguments;
      if (callee.kind === ts.SyntaxKind.ImportKeyword) add(first);
      else if (ts.isIdentifier(callee) && callee.text === "require") add(first);
      else if (
        ts.isPropertyAccessExpression(callee) &&
        isImportMeta(callee.expression) &&
        callee.name.text === "resolve"
      )
        add(first);
      else if (
        ts.isPropertyAccessExpression(callee) &&
        ts.isIdentifier(callee.expression) &&
        callee.expression.text === "vi" &&
        MODULE_PATH_METHODS.has(callee.name.text)
      )
        add(first);
    } else if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "URL"
    ) {
      const [first, second] = node.arguments ?? [];
      if (
        second &&
        ts.isPropertyAccessExpression(second) &&
        second.name.text === "url" &&
        isImportMeta(second.expression)
      )
        add(first);
    } else if (
      (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) &&
      isImportMeta(node.expression) &&
      isGlobAccess(node)
    )
      found.push({ line: lineOf(node), problem: GLOB });
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

/** `{ line, spec }` for every `@import` in the stylesheet `text`, `{ line, problem }` for one that cannot be read. */
function styleImports(text) {
  const found = [];
  const lineAt = (at) => text.slice(0, at).split("\n").length;
  /** The index after the whitespace and comments from `at`, or -1 for an unterminated comment. */
  const skipBlank = (at) => {
    for (;;) {
      at += /^\s*/.exec(text.slice(at))[0].length;
      if (!text.startsWith("/*", at)) return at;
      const end = text.indexOf("*/", at + 2);
      if (end < 0) return -1;
      at = end + 2;
    }
  };
  /** `{ value, end }` of the quoted string at `at`, a backslash escaping the next character; undefined when unterminated. */
  const readString = (at) => {
    for (let i = at + 1; i < text.length; i++) {
      if (text[i] === "\\") i++;
      else if (text[i] === text[at])
        return { value: text.slice(at + 1, i).replace(/\\(.)/gs, "$1"), end: i + 1 };
    }
    return undefined;
  };
  /** `{ value, end }` of the specifier an `@import` has from `at`, or undefined when it is not one of the readable forms. */
  const readSpecifier = (from) => {
    const at = skipBlank(from);
    if (at < 0) return undefined;
    if (/["']/.test(text[at])) return readString(at);
    const url = /^url\(/i.exec(text.slice(at));
    if (!url) return undefined;
    const open = skipBlank(at + url[0].length);
    if (open < 0) return undefined;
    if (/["']/.test(text[open])) {
      const string = readString(open);
      const close = string && skipBlank(string.end);
      return close >= 0 && text[close] === ")"
        ? { value: string.value, end: close + 1 }
        : undefined;
    }
    const end = text.indexOf(")", open);
    const value = end < 0 ? "" : text.slice(open, end).trim();
    return value && !/[\s"'(]/.test(value) ? { value, end: end + 1 } : undefined;
  };
  const atImport = /@import(?![\w\u0080-￿\\-])/iy;
  for (let i = 0; i < text.length;) {
    if (text.startsWith("/*", i)) {
      const end = text.indexOf("*/", i + 2);
      if (end < 0) break;
      i = end + 2;
    } else if (/["']/.test(text[i])) {
      const string = readString(i);
      if (!string) break;
      i = string.end;
    } else if (((atImport.lastIndex = i), atImport.test(text))) {
      let specifier = readSpecifier(i + "@import".length);
      // Escapes are not decoded, so a specifier with one cannot be followed.
      if (specifier && text.slice(i, specifier.end).includes("\\")) specifier = undefined;
      found.push(
        specifier
          ? { line: lineAt(i), spec: specifier.value }
          : { line: lineAt(i), problem: UNREADABLE },
      );
      i = specifier ? specifier.end : i + "@import".length;
    } else i++;
  }
  return found;
}

/** The unit of a repo-relative path under `frontend/src`: its first folder, or the root file's name without extension. */
function frontendUnit(file) {
  const [first] = file.slice(FRONTEND.length + 1).split("/");
  return first.replace(SOURCE_EXTENSION, "");
}

/** The layer name holding `unit`, or undefined. */
function layerOfUnit(unit) {
  return FRONTEND_LAYER_NAMES.find((layer) => FRONTEND_LAYERS[layer].includes(unit));
}

/**
 * The repo-relative path (without extension) that the specifier `spec` in `file` names, or undefined when it names a
 * package or lies outside `frontend/src`. A `reference` path is always a file path.
 */
function importedPath(file, spec, scanned, reference) {
  spec = spec.split(/[?#]/)[0];
  const dir = path.posix.dirname(file);
  // A schemeless CSS specifier is relative to the file when its first segment names something local, else a package.
  if (file.endsWith(".css") && /^[^./:][^:]*$/.test(spec)) {
    const first = path.posix.join(dir, spec.split("/")[0]);
    const local = [...scanned].some(
      (source) =>
        source === first ||
        source.replace(SOURCE_EXTENSION, "") === first ||
        source.startsWith(`${first}/`),
    );
    if (local) spec = `./${spec}`;
  }
  let base;
  if (reference || /^\.\.?\//.test(spec)) base = dir;
  else return undefined;
  const target = path.posix.normalize(path.posix.join(base, spec)).replace(SOURCE_EXTENSION, "");
  return target.startsWith(`${FRONTEND}/`) ? target : undefined;
}

/** The problems in `sources` (`{ file, text }` frontend files, `file` relative to the repository), one sentence each. */
function checkFrontendLayers(sources) {
  const problems = [];
  const unlisted = new Set();
  const scanned = new Set(sources.map((source) => source.file));
  const addToLayer = (unit) =>
    `add ${unit} to a layer in FRONTEND_LAYERS (scripts/check-frontend-layers.cjs)`;
  for (const { file, text } of sources) {
    const unit = frontendUnit(file);
    const layer = layerOfUnit(unit);
    if (layer === undefined) {
      if (!unlisted.has(unit)) {
        unlisted.add(unit);
        problems.push(`${file}: ${addToLayer(unit)}`);
      }
      continue;
    }
    const imports = file.endsWith(".css") ? styleImports(text) : scriptImports(file, text);
    for (const { line, spec, problem, reference } of imports) {
      if (!problem && isIgnored(spec)) continue;
      const own =
        problem ??
        (isAbsolute(spec) ? ABSOLUTE : undefined) ??
        (isUnknownScheme(spec) ? UNKNOWN_SCHEME : undefined) ??
        (/\\/.test(spec) || (!isNodeOrVirtual(spec) && spec.includes("%"))
          ? BACKSLASH
          : undefined) ??
        (!reference && hasDotSegment(spec) ? PACKAGE_PATH : undefined);
      if (own) {
        problems.push(`${file}:${line} ${own}`);
        continue;
      }
      const target = importedPath(file, spec, scanned, reference);
      const targetUnit = target && frontendUnit(target);
      if (!targetUnit) continue;
      const targetLayer = layerOfUnit(targetUnit);
      if (targetLayer === undefined) {
        problems.push(`${file}:${line} imports ${target}; ${addToLayer(targetUnit)}`);
      } else if (FRONTEND_LAYER_NAMES.indexOf(targetLayer) > FRONTEND_LAYER_NAMES.indexOf(layer)) {
        problems.push(
          `${file}:${line} imports ${target}, but ${layer} is below ${targetLayer}; move the shared code to ${layer} or lower (UI that features share goes in ${FRONTEND}/kit/)`,
        );
      }
    }
  }
  return problems;
}

module.exports = { checkFrontendLayers, FRONTEND, SOURCE_FILE };
