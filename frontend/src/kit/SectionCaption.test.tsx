import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SectionCaption } from "./SectionCaption";

afterEach(cleanup);

describe("section caption", () => {
  it("shows the count, and folds only through its button", () => {
    const onToggle = vi.fn();
    render(
      <SectionCaption heading="Sources" count={3} aside="local" fold={{ open: true, onToggle }} />,
    );
    const heading = screen.getByRole("heading", { name: "Sources · 3" });
    fireEvent.click(heading);
    expect(onToggle).not.toHaveBeenCalled();
    const fold = screen.getByRole("button", { name: "Sources" });
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(fold);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
