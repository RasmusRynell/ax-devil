import { expect, it, vi } from "vite-plus/test";
import { writeClipboard } from "./clipboard";

it("does not fall back to copying a superseded value after a delayed clipboard refusal", async () => {
  let release!: () => void;
  const refused = new Promise<void>((_, reject) => {
    release = () => reject(Error("permission refused"));
  });
  const fallback = vi.fn(() => true);
  vi.stubGlobal("navigator", { clipboard: { writeText: () => refused } });
  Object.defineProperty(document, "execCommand", { value: fallback, configurable: true });
  try {
    let current = true;
    const copied = writeClipboard("superseded value", () => current);
    current = false;
    release();
    expect(await copied).toBe(false);
    expect(fallback).not.toHaveBeenCalled();
  } finally {
    Reflect.deleteProperty(document, "execCommand");
    vi.unstubAllGlobals();
  }
});
