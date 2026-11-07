import { afterEach, describe, expect, it } from "vite-plus/test";
import { uiZoom } from "./uiZoom";

afterEach(() => {
  delete window.axDevilWindow;
});

describe("page zoom", () => {
  it("is the desktop shell's factor, read each time, and 1 where there is none", () => {
    expect(uiZoom()).toBe(1);
    let factor = 1.5;
    window.axDevilWindow = { zoomFactor: () => factor } as typeof window.axDevilWindow;
    expect(uiZoom()).toBe(1.5);
    factor = 2;
    expect(uiZoom()).toBe(2);
  });
});
