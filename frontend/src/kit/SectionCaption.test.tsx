import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SectionCaption } from "./SectionCaption";

afterEach(cleanup);

describe("section caption", () => {
  it("shows the count and note, and folds once from its text or its button but never from its action", () => {
    const onToggle = vi.fn();
    const onAdd = vi.fn();
    render(
      <SectionCaption
        heading="Sources"
        count={3}
        note="local"
        aside="metadata.json"
        action={<button onClick={onAdd}>Add</button>}
        fold={{ open: true, onToggle }}
      />,
    );
    fireEvent.click(screen.getByRole("heading", { name: "Sources 3 · local" }));
    expect(onToggle).toHaveBeenCalledTimes(1);
    const fold = screen.getByRole("button", { name: "Sources" });
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(fold);
    expect(onToggle).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledTimes(2);
  });
});
