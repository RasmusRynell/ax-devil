// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { paneBudget } from "./budget";
import { BUFFER_BUDGET_BYTES } from "./streamDeps";

describe("a window's frame budget", () => {
  it("is held whole by a lone pane and divided equally, in whole bytes, by more", () => {
    expect(paneBudget(1)).toBe(BUFFER_BUDGET_BYTES);
    expect(paneBudget(4)).toBe(BUFFER_BUDGET_BYTES / 4);
    expect(paneBudget(3) * 3).toBeLessThanOrEqual(BUFFER_BUDGET_BYTES);
    expect(Number.isInteger(paneBudget(3))).toBe(true);
  });

  it("never divides by none", () => {
    expect(paneBudget(0)).toBe(BUFFER_BUDGET_BYTES);
  });
});
