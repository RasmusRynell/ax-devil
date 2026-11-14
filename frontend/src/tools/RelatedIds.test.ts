import { describe, expect, it } from "vite-plus/test";
import { relatedText } from "./RelatedIds";

const chars = (text: string) => text.length;

describe("relatedText", () => {
  const ids = ["alpha-0000001", "beta-0000002", "gamma-0000003"];

  it("shows the ids whole when they fit, else shortens each in the middle, else leaves some out as +N", () => {
    expect(relatedText(ids, 80, chars)).toBe("alpha-0000001, beta-0000002, gamma-0000003");

    const shortened = relatedText(ids, 36, chars);
    expect(shortened).toContain("…");
    expect(shortened.length).toBeLessThanOrEqual(36);
    // The end of an id tells it from the others.
    expect(shortened).toMatch(/0001, .*0002, .*0003$/);

    expect(relatedText(ids, 20, chars)).toBe("alpha-0000001, +2");
    // One id is always shown, however narrow.
    expect(relatedText(ids, 6, chars)).toMatch(/….*\+2$/);
  });
});
