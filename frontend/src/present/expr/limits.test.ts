// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { FILTER } from "./check";
import { compileExpression } from "./compile";
import { MAX_DEPTH, MAX_EXPRESSION_LENGTH, MAX_NODES } from "./parse";

/** The one diagnostic of an expression that must not compile. */
function refused(text: string) {
  const result = compileExpression(text, FILTER, "bool");
  if (result.ok) throw new Error("compiled");
  expect(result.diagnostics).toHaveLength(1);
  return result.diagnostics[0];
}

describe("expression limits", () => {
  it("reports a deeply nested expression instead of overflowing the stack", () => {
    const text = `${"(".repeat(1000)}true${")".repeat(1000)}`;
    expect(refused(text)).toMatchObject({ code: "limit", span: [MAX_DEPTH, MAX_DEPTH + 1] });
  });

  it.each([
    ["prefix operators", `${"!".repeat(MAX_DEPTH + 1)}true`],
    ["calls", `${"abs(".repeat(MAX_DEPTH + 1)}1${")".repeat(MAX_DEPTH + 1)} > 0`],
    ["lists", `${"[".repeat(MAX_DEPTH + 1)}1${"]".repeat(MAX_DEPTH + 1)}`],
    ["a chain of operators", `${"1 + ".repeat(MAX_DEPTH + 1)}1 > 0`],
  ])("limits the depth of %s", (_name, text) => {
    expect(refused(text).code).toBe("limit");
  });

  it("accepts nesting up to the limit", () => {
    const depth = MAX_DEPTH - 1;
    const text = `${"(".repeat(depth)}true${")".repeat(depth)}`;
    expect(compileExpression(text, FILTER, "bool").ok).toBe(true);
  });

  it("refuses text longer than the limit before reading it, at the excess", () => {
    const text = `${" ".repeat(MAX_EXPRESSION_LENGTH)}true`;
    expect(refused(text)).toMatchObject({
      code: "limit",
      span: [MAX_EXPRESSION_LENGTH, text.length],
    });
  });

  it("refuses an expression with too many parts", () => {
    const text = `[${Array.from({ length: MAX_NODES + 1 }, () => "1").join(",")}] == null`;
    expect(text.length).toBeLessThan(MAX_EXPRESSION_LENGTH);
    expect(refused(text).code).toBe("limit");
  });
});

describe("internal failure", () => {
  it("becomes a diagnostic, never an exception", () => {
    const scope = new Proxy(FILTER, {
      get() {
        throw new RangeError("boom");
      },
    });
    const result = compileExpression("score > 1", scope, "bool", "/x");
    expect(result).toMatchObject({
      ok: false,
      diagnostics: [
        {
          code: "internal",
          pointer: "/x",
          message: expect.stringContaining("internal error compiling"),
        },
      ],
    });
  });
});
