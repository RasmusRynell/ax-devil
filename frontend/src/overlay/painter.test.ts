import { describe, expect, it } from "vite-plus/test";
import { OverlayPainter, type LaneDrawing } from "./painter";

/** A context that accepts every call and records it; the named calls fail once `failing` holds them. */
function recordingContext() {
  const calls: [string, ...unknown[]][] = [];
  const failing = new Set<string>();
  const gl = new Proxy(
    { drawingBufferWidth: 100, drawingBufferHeight: 100 },
    {
      get(target, name: string) {
        if (name in target) return target[name as keyof typeof target];
        // Constants are numbers named in capitals, so each is told apart by its name's length; every function
        // answers with an empty object, a framebuffer that is complete, or a shader that cannot be made.
        if (name === name.toUpperCase()) return name.length;
        return (...args: unknown[]) => {
          calls.push([name, ...args]);
          if (failing.has(name)) {
            if (name === "createShader") return null;
            throw new Error(`${name} failed`);
          }
          if (name === "checkFramebufferStatus") return "FRAMEBUFFER_COMPLETE".length;
          return name.startsWith("get") ? true : {};
        };
      },
    },
  ) as unknown as WebGL2RenderingContext;
  return { gl, calls, failing };
}

const drawing = {
  lane: 0,
  version: 1,
  view: { width: 100, height: 100, dpr: 1 },
  opacity: 0.5,
  quads: {
    quads: { count: 1, subarray: () => new Float32Array(12) },
    fills: { length: 0, view: () => new Float32Array(0) },
    layerCount: 0,
  },
} as unknown as LaneDrawing;
const cell = { x: 0, y: 0, width: 100, height: 100 };

describe("OverlayPainter", () => {
  it("changes no state when the fade layer cannot be set up", () => {
    const { gl, calls, failing } = recordingContext();
    const painter = new OverlayPainter(gl);
    calls.length = 0;
    failing.add("createShader");
    expect(() => painter.draw(drawing, cell, cell)).toThrow();
    const names = calls.map(([name]) => name);
    expect(names).not.toContain("enable");
    expect(names).not.toContain("viewport");
    expect(names).not.toContain("bindFramebuffer");
  });

  it("leaves the canvas bound and the state off when drawing into the layer fails", () => {
    const { gl, calls, failing } = recordingContext();
    const painter = new OverlayPainter(gl);
    failing.add("drawArraysInstanced");
    calls.length = 0;
    expect(() => painter.draw(drawing, cell, cell)).toThrow("drawArraysInstanced failed");
    const bound = calls.filter(([name]) => name === "bindFramebuffer");
    expect(bound.at(-1)?.[2]).toBeNull();
    const disabled = calls.filter(([name]) => name === "disable").map(([, flag]) => flag);
    expect(disabled).toEqual(
      expect.arrayContaining(["SCISSOR_TEST", "BLEND", "STENCIL_TEST"].map((flag) => flag.length)),
    );
  });
});
