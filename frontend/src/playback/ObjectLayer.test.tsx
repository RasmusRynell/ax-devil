import { cleanup, render } from "@testing-library/react";
import { act, createRef } from "react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { ObjectLayer, type ObjectLayerHandle } from "./ObjectLayer";
import type { Outline } from "./outlines";
import type { Geometry } from "./renderer";

afterEach(cleanup);

/** Mounts the layer with `outlines` reported by the pictures' geometry. */
function show(outlines: Outline[]) {
  const handle = createRef<ObjectLayerHandle>();
  render(
    <ObjectLayer selection={{ lane: 0, source: 0, entity: "7" }} objectDetails handle={handle} />,
  );
  act(() => handle.current?.drawn({ boxes: () => outlines } as unknown as Geometry));
  const box = document.querySelector<HTMLElement>("[data-outline]");
  return { box, clip: box?.parentElement };
}

describe("object layer outlines", () => {
  it("keeps the contour of an object reaching past its cell and cuts only the stroke", () => {
    // The object starts 10px left of the 100x100 cell at (50, 20).
    const { box, clip } = show([
      {
        lane: 0,
        rect: { x: 40, y: 30, width: 60, height: 40 },
        clip: { x: 50, y: 20, width: 100, height: 100 },
        kind: "selected",
        scale: 100,
      },
    ]);
    expect(clip?.style).toMatchObject({
      left: "50px",
      top: "20px",
      width: "100px",
      height: "100px",
    });
    expect(clip?.className).toContain("overflow-hidden");
    // In the clip's own coordinates the box is the contour (40 - 50, 30 - 20), neither clamped nor inset.
    expect([box?.style.left, box?.style.top]).toEqual(["-10px", "10px"]);
    expect([box?.style.width, box?.style.height]).toEqual(["60px", "40px"]);
  });

  it("strokes the contour itself, so the stroke straddles it", () => {
    const { box } = show([
      {
        lane: 0,
        rect: { x: 0, y: 0, width: 50, height: 50 },
        clip: { x: 0, y: 0, width: 100, height: 100 },
        kind: "selected",
        scale: 100,
      },
    ]);
    // The stroked rectangle fills the contour box exactly; a stroke on its edge reaches half its width to each side.
    const stroked = box?.querySelector("svg rect");
    expect([stroked?.getAttribute("width"), stroked?.getAttribute("height")]).toEqual([
      "100%",
      "100%",
    ]);
    expect(stroked?.getAttribute("x")).toBeNull();
    expect(Number(stroked?.getAttribute("stroke-width"))).toBeGreaterThan(0);
  });
});
