import { describe, expect, it } from "vite-plus/test";
import { videoFrame } from "./fakePlayback";
import type { VideoFrame } from "./protocol";
import { VideoRenderer } from "./renderer";

/**
 * A WebGL2 context that records its calls. Constants read as their own names, so a recorded call is readable, and
 * everything else is a call that answers like a context that accepts it.
 */
function recordingGl() {
  const calls: [string, ...unknown[]][] = [];
  const answers: Record<string, unknown> = { getShaderParameter: true, getProgramParameter: true };
  const gl = new Proxy({} as Record<string, unknown>, {
    get: (_target, name: string) => {
      if (/^[A-Z][A-Z0-9_]*$/.test(name)) return name;
      return (...args: unknown[]) => {
        calls.push([name, ...args]);
        return name in answers ? answers[name] : name.startsWith("create") ? { made: name } : null;
      };
    },
  });
  const canvas = { width: 8, height: 8, getContext: () => gl } as unknown as HTMLCanvasElement;
  return { canvas, calls };
}

describe("video renderer", () => {
  it("clears to the canvas near-black and keeps the video sampled nearest on a single level, so zoomed pixels stay hard", () => {
    const { canvas, calls } = recordingGl();
    const renderer = new VideoRenderer(canvas, 1);
    const frame: VideoFrame = {
      ...videoFrame(1),
      planes: [new Uint8Array(16), new Uint8Array(4), new Uint8Array(4)],
    };
    calls.length = 0;
    renderer.present(frame);

    const clear = calls.find(([name]) => name === "clearColor");
    expect(clear?.slice(1, 4).map((value) => Math.round((value as number) * 255))).toEqual([
      0x0e, 0x0f, 0x10,
    ]);

    // Texture 0 is the full-resolution luma plane: the pixels the user zooms into.
    const filters = calls
      .filter(([name, , parameter]) => name === "texParameteri" && /FILTER/.test(String(parameter)))
      .slice(0, 2);
    expect(filters.map(([, , , value]) => value)).toEqual(["NEAREST", "NEAREST"]);
    const storage = calls.filter(([name]) => name === "texStorage2D");
    expect(storage.length).toBeGreaterThanOrEqual(3);
    expect(storage.every(([, , levels]) => levels === 1)).toBe(true);
    expect(calls.some(([name]) => name === "generateMipmap")).toBe(false);
  });
});
