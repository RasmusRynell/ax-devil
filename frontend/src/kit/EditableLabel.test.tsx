import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { EditableLabel } from "./EditableLabel";

afterEach(cleanup);

function setup(value = "Alpha") {
  const onEdit = vi.fn();
  const onDone = vi.fn();
  const view = (text: string) => (
    <EditableLabel value={text} label="Name" onEdit={onEdit} onDone={onDone} />
  );
  const { rerender } = render(view(value));
  fireEvent.click(screen.getByRole("button", { name: value }));
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Name" });
  return { onEdit, onDone, input, rerender: (text: string) => rerender(view(text)) };
}

describe("editable label", () => {
  it("opens with all of the text selected and publishes each change", () => {
    const { input, onEdit } = setup();
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 5]);
    fireEvent.change(input, { target: { value: "Alp" } });
    fireEvent.change(input, { target: { value: "Alpine" } });
    expect(onEdit.mock.calls).toEqual([["Alp"], ["Alpine"]]);
  });

  it("keeps the typed text when the value changes during the edit", () => {
    const { input, rerender } = setup();
    fireEvent.change(input, { target: { value: "Alpine" } });
    rerender("Other");
    expect(input.value).toBe("Alpine");
  });

  it("restores the entry text on Escape only when it changed", () => {
    const first = setup();
    fireEvent.keyDown(first.input, { key: "Escape" });
    expect(first.onEdit).not.toHaveBeenCalled();
    expect(first.onDone).toHaveBeenCalledTimes(1);
    cleanup();

    const second = setup();
    fireEvent.change(second.input, { target: { value: "Alpine" } });
    fireEvent.keyDown(second.input, { key: "Escape" });
    expect(second.onEdit).toHaveBeenLastCalledWith("Alpha");
    expect(second.onDone).toHaveBeenCalledTimes(1);
  });

  it("ends on Enter without a further edit and shows the value again, focused", () => {
    const { input, onEdit, onDone, rerender } = setup();
    fireEvent.change(input, { target: { value: "Alpine" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledTimes(1);
    rerender("Alpine");
    const button = screen.getByRole("button", { name: "Alpine" });
    expect(document.activeElement).toBe(button);
  });

  it("ignores Enter while an input method is composing", () => {
    const { input, onDone } = setup();
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onDone).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
