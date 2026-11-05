import { describe, expect, it } from "vite-plus/test";
import { OverlayPainter, type LaneDrawing } from "./painter";

/**
 * A context that accepts every call and records it, with the capabilities switched on and the framebuffer bound at
 * the end. Calls named in `throws` throw, and creations named in `nothing` answer null, as under resource pressure.
 */
function recordingContext() {
  const calls: [string, ...unknown[]][] = [];
  const throws = new Set<string>();
  const nothing = new Set<string>();
  const enabled = new Set<unknown>();
  const bound: { framebuffer: unknown } = { framebuffer: null };
  // Each constant is its own number, so a flag is told from another by value.
  const constants = new Map<string, number>();
  const constant = (name: string) => {
    if (!constants.has(name)) constants.set(name, constants.size + 1);
    return constants.get(name);
  };
  const gl = new Proxy(
    { drawingBufferWidth: 100, drawingBufferHeight: 100 },
    {
      get(target, name: string) {
        if (name in target) return target[name as keyof typeof target];
        if (name === name.toUpperCase()) return constant(name);
        return (...args: unknown[]) => {
          calls.push([name, ...args]);
          if (nothing.has(name)) return null;
          if (throws.has(name)) throw new Error(`${name} failed`);
          if (name === "enable") enabled.add(args[0]);
          if (name === "disable") enabled.delete(args[0]);
          if (name === "bindFramebuffer") bound.framebuffer = args[1];
          if (name === "checkFramebufferStatus") return constant("FRAMEBUFFER_COMPLETE");
          return name.startsWith("get") ? true : {};
        };
      },
    },
  ) as unknown as WebGL2RenderingContext;
  return { gl, calls, throws, nothing, enabled, bound };
}

const quads = {
  quads: { count: 1, subarray: () => new Float32Array(12) },
  fills: { length: 0, view: () => new Float32Array(0) },
  layerCount: 0,
};
/** One triangle's fill: the header and three corners with their ring. */
const fill = new Float32Array(3 + 11 * 5);
fill[0] = 3;
const drawn = (parts: object) =>
  ({
    lane: 0,
    version: 1,
    view: { width: 100, height: 100, dpr: 1 },
    opacity: 0.5,
    quads: { ...quads, ...parts },
  }) as unknown as LaneDrawing;
const cell = { x: 0, y: 0, width: 100, height: 100 };

describe("OverlayPainter", () => {
  it.each([
    ["a shader cannot be made", "nothing", "createShader"],
    ["a framebuffer cannot be made", "nothing", "createFramebuffer"],
    ["the layer is not complete", "throws", "checkFramebufferStatus"],
  ] as const)("changes no state when the fade layer cannot be set up: %s", (_, kind, call) => {
    const context = recordingContext();
    const painter = new OverlayPainter(context.gl);
    context.calls.length = 0;
    context[kind].add(call);
    expect(() => painter.draw(drawn({}), cell, cell)).toThrow();
    expect(context.enabled.size).toBe(0);
    expect(context.calls.map(([name]) => name)).not.toContain("viewport");
    expect(context.bound.framebuffer).toBeNull();
  });

  it.each([
    ["a quad", "drawArraysInstanced", {}],
    [
      "a polygon's stencil pass",
      "drawArrays",
      { fills: { length: fill.length, view: () => fill } },
    ],
  ] as const)(
    "leaves the canvas bound and the state off when drawing %s fails",
    (_, call, parts) => {
      const context = recordingContext();
      const painter = new OverlayPainter(context.gl);
      context.calls.length = 0;
      context.throws.add(call);
      expect(() => painter.draw(drawn(parts), cell, cell)).toThrow(`${call} failed`);
      // The layer was bound while the failure happened.
      expect(context.calls.filter(([name]) => name === "bindFramebuffer").length).toBeGreaterThan(
        1,
      );
      expect(context.bound.framebuffer).toBeNull();
      expect(context.enabled.size).toBe(0);
    },
  );
});
