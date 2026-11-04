import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FormCombo } from "./Form";

afterEach(cleanup);

const OPTIONS = [
  { value: "", label: "Camera default" },
  { value: "1280x720", label: "1280x720" },
  { value: "none", label: "Could not read resolutions", disabled: true },
];

describe("form combo", () => {
  it("keeps any typed text, puts a chosen line in the box and never chooses a status line", () => {
    const onChange = vi.fn();
    render(<FormCombo label="Resolution" value="800x600" options={OPTIONS} onChange={onChange} />);
    const box = screen.getByLabelText("Resolution");
    fireEvent.change(box, { target: { value: "640x480" } });
    expect(onChange).toHaveBeenLastCalledWith("640x480");

    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: "Could not read resolutions" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith("1280x720");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens upward when the window has more room above the box than below it", () => {
    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ left: 10, top: 700, bottom: 730, width: 200 } as DOMRect);
    const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(400);
    render(<FormCombo label="Resolution" value="" options={OPTIONS} onChange={() => {}} />);
    fireEvent.keyDown(screen.getByLabelText("Resolution"), { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    expect(list.style.top).toBe("auto");
    expect(list.style.bottom).toBe(`${window.innerHeight - 700}px`);
    expect(parseFloat(list.style.maxHeight)).toBeLessThanOrEqual(window.innerHeight * 0.4);
    rect.mockRestore();
    height.mockRestore();
  });
});
