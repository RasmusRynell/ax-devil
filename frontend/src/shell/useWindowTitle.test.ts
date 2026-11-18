import { describe, expect, it } from "vite-plus/test";
import { windowTitle } from "./useWindowTitle";

describe("windowTitle", () => {
  it("names the workspace, marks unsaved changes, and falls back to Untitled", () => {
    expect(windowTitle({ name: "Lobby", modified: false })).toBe("Lobby — ax-devil");
    expect(windowTitle({ name: "Lobby", modified: true })).toBe("● Lobby — ax-devil");
    expect(windowTitle({ name: null, modified: false })).toBe("Untitled — ax-devil");
  });
});
