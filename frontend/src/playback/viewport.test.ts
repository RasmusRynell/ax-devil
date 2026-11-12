// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type { Viewport } from "../lanes/contract";
import {
  FIT,
  ZOOM_MAX,
  ZOOM_STEP,
  fitPicture,
  intersection,
  panBy,
  pictureRect,
  regionOf,
  toImage,
  viewportOf,
  zoomAt,
} from "./viewport";

const image = { width: 1600, height: 900 };
const cell = { x: 100, y: 50, width: 800, height: 450 };

/** The image point (0 to 1 across and down) under a point of the cell as `v` shows it. */
function under(v: Viewport, x: number, y: number, c = cell) {
  return toImage(pictureRect(c, image, v), x, y);
}

describe("viewport", () => {
  it("fits the whole picture when not zoomed", () => {
    expect(pictureRect(cell, image, FIT)).toEqual(fitPicture(cell, image));
    expect(fitPicture({ x: 0, y: 0, width: 800, height: 800 }, image)).toEqual({
      x: 0,
      y: 175,
      width: 800,
      height: 450,
    });
  });

  it("keeps the image point under the pointer in place while zooming, in and out", () => {
    for (const [x, y] of [
      [500, 275],
      [160, 90],
      [880, 480],
    ]) {
      let v: Viewport = FIT;
      const before = under(FIT, x, y);
      for (const factor of [ZOOM_STEP, ZOOM_STEP, ZOOM_STEP, 1 / ZOOM_STEP]) {
        v = zoomAt(v, cell, image, x, y, factor);
        const now = under(v, x, y);
        expect(now?.x).toBeCloseTo(before?.x ?? NaN, 2);
        expect(now?.y).toBeCloseTo(before?.y ?? NaN, 2);
      }
    }
  });

  it("zooms between 1 and the maximum and is not zoomed at 1", () => {
    let v: Viewport = FIT;
    for (let i = 0; i < 40; i++) v = zoomAt(v, cell, image, 500, 275, ZOOM_STEP);
    expect(v.zoom).toBe(ZOOM_MAX);
    expect(zoomAt(v, cell, image, 500, 275, ZOOM_STEP)).toBe(v);
    for (let i = 0; i < 40; i++) v = zoomAt(v, cell, image, 500, 275, 1 / ZOOM_STEP);
    expect(v).toBe(FIT);
  });

  it("keeps the zoomed picture over the whole cell and centres what fits", () => {
    const far = pictureRect(cell, image, { zoom: 2, panX: 5, panY: -5 });
    expect(far).toEqual({ x: 100, y: -400, width: 1600, height: 900 });
    // Taller than the picture's aspect: the short axis fits at 1.5 and stays centred whatever the pan.
    const square = { x: 0, y: 0, width: 800, height: 800 };
    const rect = pictureRect(square, image, { zoom: 1.5, panX: 0.1, panY: 0.3 });
    expect(Math.abs(rect.y + rect.height / 2 - 400)).toBeLessThanOrEqual(1);
    expect(rect.height).toBe(675);
  });

  it("pans by pixels of the zoomed picture and stops at its edges", () => {
    const zoomed = { zoom: 2, panX: 0, panY: 0 };
    const moved = panBy(zoomed, cell, image, 100, -50);
    expect(pictureRect(cell, image, moved)).toEqual({ x: -200, y: -225, width: 1600, height: 900 });
    expect(panBy(zoomed, cell, image, 1e6, 1e6)).toEqual({ zoom: 2, panX: 0.25, panY: 0.25 });
    expect(panBy(FIT, cell, image, 100, 100)).toBe(FIT);
  });

  it("shows the same part of the image in cells of any size", () => {
    const v = { zoom: 3, panX: 0.1, panY: -0.05 };
    const small = { x: 10, y: 20, width: 400, height: 225 };
    const centre = (c: typeof cell) => {
      const r = pictureRect(c, image, v);
      return [(c.x + c.width / 2 - r.x) / r.width, (c.y + c.height / 2 - r.y) / r.height];
    };
    expect(centre(small)[0]).toBeCloseTo(centre(cell)[0], 2);
    expect(centre(small)[1]).toBeCloseTo(centre(cell)[1], 2);
  });

  it("maps a point of the picture to image fractions and outside points to none", () => {
    const picture = pictureRect(cell, image, { zoom: 2, panX: 0.1, panY: 0 });
    expect(toImage(picture, picture.x + picture.width / 4, picture.y + picture.height / 2)).toEqual(
      {
        x: 0.25,
        y: 0.5,
      },
    );
    expect(toImage(picture, picture.x - 1, picture.y + 5)).toBeNull();
    expect(intersection(picture, cell)).toEqual(cell);
    expect(intersection(cell, { x: 2000, y: 0, width: 5, height: 5 })).toBeNull();
  });

  it("fits a shared region inside a cell of another shape, showing at least that region", () => {
    const region = regionOf({ zoom: 4, panX: 0.2, panY: -0.1 }, cell, image)!;
    for (const other of [
      { x: 0, y: 0, width: 300, height: 600 },
      { x: 0, y: 0, width: 900, height: 200 },
      { x: 0, y: 0, width: 400, height: 225 },
    ]) {
      const shown = regionOf(viewportOf(region, other, image), other, image)!;
      const slack = 0.01;
      expect(shown.x).toBeLessThanOrEqual(region.x + slack);
      expect(shown.y).toBeLessThanOrEqual(region.y + slack);
      expect(shown.x + shown.w).toBeGreaterThanOrEqual(region.x + region.w - slack);
      expect(shown.y + shown.h).toBeGreaterThanOrEqual(region.y + region.h - slack);
    }
    expect(regionOf(FIT, cell, image)).toBeUndefined();
    expect(viewportOf(undefined, cell, image)).toBe(FIT);
  });
});
