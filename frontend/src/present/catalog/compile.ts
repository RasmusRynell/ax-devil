import { CONDITION, CONSTANT, ENTITY, RELATION } from "../expr/check";
import { didYouMean, internalError } from "../expr/diagnostic";
import type { Diagnostic } from "../expr/diagnostic";
import type { Scalar } from "../expr/compiled";
import { FIELDS } from "../expr/fields";
import { TypeVar } from "../expr/types";
import { arrayAt, closedObject, objectAt, pointerTo, stringAt } from "./document";
import { compileMark } from "./marks";
import { duplicateKeys, MAX_DUPLICATES } from "./locate";
import { LAYER, LAYER_TARGETS, ROOT, RULE } from "./shape";
import { Session } from "./session";
import { FEATURES, type Feature, type LayerSpec, type MarkSpec } from "./types";
import type { CompiledCatalog } from "./types";
import { type VarDecl, allocate, lowerVars, orderVars } from "./vars";

/** What compiling a catalog gives: the catalog, or every problem found. */
export type CatalogResult =
  | { readonly ok: true; readonly catalog: CompiledCatalog }
  | { readonly ok: false; readonly diagnostics: readonly Diagnostic[] };

/** The schema number this build reads. */
export const CATALOG_SCHEMA = 4;

/** Words a variable may not use: field roots, the language's keywords and function names are all taken. */
const RESERVED = new Set([
  "null",
  "true",
  "false",
  "in",
  "attr",
  "source",
  "target",
  "image",
  ...Object.keys(FIELDS).map((name) => name.split(".")[0]),
]);

let nextUid = 1;

class CatalogCompiler {
  private readonly session = new Session();
  private readonly decls = new Map<string, VarDecl>();
  private readonly themes: string[] = [];
  private ruleCount = 0;
  private markCount = 0;
  private features = 0;
  private readonly rules: Scalar[] = [];
  private readonly layers: LayerSpec[] = [];

  private get diag() {
    return this.session.diag;
  }

  compile(document: unknown): CatalogResult {
    const root = closedObject(document, "", ROOT.allowed, ROOT.required, this.diag);
    if (!root) return this.failed();
    if ("schema" in root && root.schema !== CATALOG_SCHEMA) {
      this.diag.add(
        "schema",
        { pointer: "/schema", spans: false },
        null,
        `catalog schema ${JSON.stringify(root.schema)}; this build reads ${CATALOG_SCHEMA}`,
      );
      return this.failed();
    }
    const [name, description] = (["name", "description"] as const).map((key) =>
      key in root ? stringAt(root[key], pointerTo("", key), this.diag) : null,
    );
    const origin = "origin" in root ? stringAt(root.origin, "/origin", this.diag) : null;
    this.declareVars(root.vars);
    this.declareThemes(root.themes);
    const rules = this.readRules(root.rules);
    const order = orderVars(this.decls, this.session);
    this.checkRules(rules);
    this.checkVars(order);
    this.checkLayers(root.layers);
    const stride = allocate(order, this.session);
    if (this.diag.list.length > 0) return this.failed();
    const programs = lowerVars(order, this.session, this.themes.length);
    this.session.finish();
    if (this.diag.list.length > 0 || name === null || description === null) return this.failed();
    this.session.unit.ctx.configure(stride, this.ruleCount);
    return {
      ok: true,
      catalog: {
        uid: nextUid++,
        name,
        description,
        origin,
        features: this.features,
        themes: this.themes,
        ctx: this.session.unit.ctx,
        rules: this.rules,
        vars: programs,
        layers: this.layers,
      },
    };
  }

  private failed(): CatalogResult {
    return { ok: false, diagnostics: this.diag.list };
  }

  private declareVars(value: unknown): void {
    if (value === undefined) return;
    const object = objectAt(value, "/vars", this.diag);
    if (!object) return;
    for (const [name, initial] of Object.entries(object)) {
      const pointer = pointerTo("/vars", name);
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || RESERVED.has(name)) {
        this.diag.add(
          "value",
          { pointer, spans: false },
          null,
          `'${name}' cannot name a variable: it is a plain name that is not a field or keyword`,
        );
        continue;
      }
      const decl: VarDecl = {
        name,
        pointer,
        type: new TypeVar(),
        initial: this.session.parse(initial, pointer),
        assignments: [],
        themed: [],
        initialChecked: null,
        assignmentsChecked: [],
        themedChecked: [],
      };
      this.decls.set(name, decl);
      this.session.unit.vars.set(name, { name, type: decl.type });
    }
  }

  private declareThemes(value: unknown): void {
    if (value === undefined) return;
    const object = objectAt(value, "/themes", this.diag);
    if (!object) return;
    for (const [theme, values] of Object.entries(object)) {
      const at = pointerTo("/themes", theme);
      const overrides = objectAt(values, at, this.diag);
      if (!overrides) continue;
      this.themes.push(theme);
      for (const [name, literal] of Object.entries(overrides)) {
        const decl = this.decls.get(name);
        if (!decl) {
          this.diag.add(
            "unknown-name",
            { pointer: pointerTo(at, name), spans: false },
            null,
            `unknown variable '${name}'${didYouMean(name, this.decls.keys())}`,
          );
          continue;
        }
        const parsed = this.session.parse(literal, pointerTo(at, name));
        if (parsed) decl.themed.push({ theme: this.themes.length, parsed });
      }
    }
  }

  private readRules(value: unknown): { when: ReturnType<Session["parse"]>; index: number }[] {
    if (value === undefined) return [];
    const list = arrayAt(value, "/rules", this.diag);
    if (!list) return [];
    this.ruleCount = list.length;
    return list.map((entry, index) => {
      const at = pointerTo("/rules", index);
      const rule = closedObject(entry, at, RULE.allowed, RULE.required, this.diag);
      const when =
        rule && "when" in rule ? this.session.parse(rule.when, pointerTo(at, "when")) : null;
      const sets =
        rule && "set" in rule ? objectAt(rule.set, pointerTo(at, "set"), this.diag) : null;
      for (const [name, assigned] of Object.entries(sets ?? {})) {
        const pointer = pointerTo(pointerTo(at, "set"), name);
        const decl = this.decls.get(name);
        if (!decl) {
          this.diag.add(
            "unknown-name",
            { pointer, spans: false },
            null,
            `unknown variable '${name}'${didYouMean(name, this.decls.keys())}`,
          );
          continue;
        }
        const parsed = this.session.parse(assigned, pointer);
        if (parsed) decl.assignments.push({ rule: index, parsed });
      }
      return { when, index };
    });
  }

  private checkRules(rules: ReturnType<CatalogCompiler["readRules"]>): void {
    for (const { when, index } of rules) {
      if (!when) continue;
      const checked = this.session.check(when, CONDITION, "bool");
      if (checked) {
        this.session.later(() => {
          const compiled = this.session.lower(checked, "bool");
          if (compiled?.type === "bool") this.rules[index] = compiled.scalar;
        });
      }
    }
  }

  private checkVars(order: readonly VarDecl[]): void {
    for (const decl of order) {
      const agree = (parsed: NonNullable<VarDecl["initial"]>, scope: typeof ENTITY) => {
        const checked = this.session.check(parsed, scope);
        return checked && this.session.agree(checked, decl.type, decl.name) ? checked : null;
      };
      if (decl.initial) decl.initialChecked = agree(decl.initial, ENTITY);
      for (const { rule, parsed } of decl.assignments) {
        const checked = agree(parsed, ENTITY);
        if (checked) decl.assignmentsChecked.push({ rule, checked });
      }
      for (const { theme, parsed } of decl.themed) {
        const checked = agree(parsed, CONSTANT);
        if (checked) decl.themedChecked.push({ theme, checked });
      }
    }
  }

  private checkLayers(value: unknown): void {
    const list = arrayAt(value, "/layers", this.diag);
    list?.forEach((entry, index) => {
      const at = pointerTo("/layers", index);
      const layer = closedObject(entry, at, LAYER.allowed, LAYER.required, this.diag);
      if (!layer) return;
      let feature: Feature | null = null;
      if ("feature" in layer) {
        const name = stringAt(layer.feature, pointerTo(at, "feature"), this.diag);
        if (name !== null && !(FEATURES as readonly string[]).includes(name)) {
          this.diag.add(
            "value",
            { pointer: pointerTo(at, "feature"), spans: false },
            null,
            `unknown feature '${name}'${didYouMean(name, FEATURES)}`,
          );
        } else if (name !== null) {
          feature = name as Feature;
          this.features |= 1 << FEATURES.indexOf(feature);
        }
      }
      let relations = false;
      if ("for" in layer) {
        const target = stringAt(layer.for, pointerTo(at, "for"), this.diag);
        if (target !== null && !(LAYER_TARGETS as readonly string[]).includes(target)) {
          this.diag.add(
            "value",
            { pointer: pointerTo(at, "for"), spans: false },
            null,
            "'for' is 'entities' or 'relations'",
          );
        }
        relations = target === "relations";
      }
      const scope = relations ? RELATION : ENTITY;
      const spec = {
        index,
        feature,
        relations,
        when: null as Scalar | null,
        marks: [] as MarkSpec[],
      };
      this.layers[index] = spec;
      if ("when" in layer) {
        this.session.field(layer.when, pointerTo(at, "when"), scope, "bool", (compiled) => {
          if (compiled.type === "bool") spec.when = compiled.scalar;
        });
      }
      const marks = arrayAt(layer.marks, pointerTo(at, "marks"), this.diag);
      marks?.forEach((mark, markIndex) => {
        const made = compileMark(
          this.session,
          mark,
          pointerTo(pointerTo(at, "marks"), markIndex),
          { index, relations, scope },
          this.markCount++,
        );
        if (made) spec.marks.push(made);
      });
    });
  }
}

/** Compile a catalog document that was already parsed from JSON. */
export function compileCatalogDocument(document: unknown): CatalogResult {
  try {
    return new CatalogCompiler().compile(document);
  } catch (error) {
    return { ok: false, diagnostics: [internalError(error)] };
  }
}

/**
 * Compile catalog JSON text; a text that is not JSON is one problem at the root. A key repeated in an object is a
 * problem, listed with the compiler's own, so a document that says two things never compiles as valid.
 */
export function compileCatalog(source: string): CatalogResult {
  let document: unknown;
  try {
    document = JSON.parse(source);
  } catch (error) {
    const message = error instanceof Error ? error.message : "not JSON";
    return {
      ok: false,
      diagnostics: [{ severity: "error", code: "syntax", pointer: "", message }],
    };
  }
  const duplicates = duplicateKeys(source);
  const result = compileCatalogDocument(document);
  if (duplicates.length === 0) return result;
  return {
    ok: false,
    diagnostics: [
      ...(result.ok ? [] : result.diagnostics),
      ...duplicates.slice(0, MAX_DUPLICATES).map(({ pointer, key, line, column }): Diagnostic => ({
        severity: "error",
        code: "duplicate-key",
        pointer,
        at: { line, column },
        message: `duplicate key '${key}'`,
      })),
      ...(duplicates.length > MAX_DUPLICATES
        ? [
            {
              severity: "error",
              code: "duplicate-key",
              pointer: "",
              message: "more duplicate keys not listed",
            } satisfies Diagnostic,
          ]
        : []),
    ],
  };
}
