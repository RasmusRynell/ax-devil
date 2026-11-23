import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Seam } from "./Seam";

afterEach(cleanup);

describe("seam", () => {
  it("takes the arrow keys for itself, so the shell does not also step the video", () => {
    const onResize = vi.fn();
    const shell = vi.fn();
    document.addEventListener("keydown", shell);
    render(
      <Seam
        seam={{
          path: [],
          axis: "row",
          rect: { x: 0, y: 0, w: 1000, h: 400 },
          offset: 500,
          least: 160,
          most: 840,
        }}
        onResize={onResize}
      />,
    );
    fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" });
    document.removeEventListener("keydown", shell);
    expect(onResize).toHaveBeenCalledWith([], expect.closeTo(5240, 0), true);
    expect(shell).not.toHaveBeenCalled();
  });
});
