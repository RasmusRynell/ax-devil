// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { decode } from "./wire";

describe("what the socket delivers", () => {
  it("reads a control message", () => {
    expect(decode('{"type":"Indexing","progress":0.5}')).toEqual({
      type: "message",
      message: { type: "Indexing", progress: 0.5 },
    });
  });

  it("reports a message that breaks the protocol, so the session can end", () => {
    expect(decode("not json")?.type).toBe("malformed");
    expect(decode(new ArrayBuffer(40))?.type).toBe("malformed");
  });
});
