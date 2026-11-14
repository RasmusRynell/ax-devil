import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, render } from "@testing-library/react";
import { HoverPicture } from "./HoverPicture";

describe("hover picture", () => {
  const bitmap = { width: 1920, height: 1080 } as ImageBitmap;
  const drawImage = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("devicePixelRatio", 2);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    drawImage.mockClear();
  });

  const card = (x: number, y: number) => {
    const { container } = render(<HoverPicture bitmap={bitmap} x={x} y={y} />);
    const box = container.firstElementChild as HTMLElement;
    return {
      canvas: container.querySelector("canvas") as HTMLCanvasElement,
      left: parseFloat(box.style.left),
      top: parseFloat(box.style.top),
      width: parseFloat(box.style.width),
      height: parseFloat(box.style.height),
    };
  };

  it("stays inside the window, on the other side of a pointer in the bottom right corner", () => {
    const [x, y] = [window.innerWidth - 10, window.innerHeight - 10];
    const { left, top, width, height } = card(x, y);
    expect(left + width).toBeLessThanOrEqual(x);
    expect(top + height).toBeLessThanOrEqual(y);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(top).toBeGreaterThanOrEqual(0);
  });

  it("never leaves the window's top left when it fits on neither side", () => {
    vi.stubGlobal("innerWidth", 100);
    vi.stubGlobal("innerHeight", 60);
    const { left, top } = card(50, 30);
    expect([left, top]).toEqual([0, 0]);
  });

  it("keeps the picture's shape and draws it at the display's pixel ratio", () => {
    const { canvas, width, height } = card(10, 10);
    expect(width / height).toBeCloseTo(bitmap.width / bitmap.height, 1);
    expect([canvas.width, canvas.height]).toEqual([width * 2, height * 2]);
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, canvas.width, canvas.height);
  });
});
