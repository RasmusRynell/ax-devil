import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Chip, ChoiceChips, SegmentedControl, ToggleChip } from "./Chips";

afterEach(cleanup);

/** Stands for the shell's global shortcuts, which listen on the document. */
function shortcuts() {
  const heard = vi.fn();
  document.addEventListener("keydown", heard);
  document.addEventListener("keyup", heard);
  return { heard, stop: () => document.removeEventListener("keydown", heard) };
}

describe("chips", () => {
  it("are focusable buttons only when clickable, and their keys do not run app shortcuts", () => {
    const onClick = vi.fn();
    const { heard, stop } = shortcuts();
    render(
      <>
        <Chip tone="caution">Static</Chip>
        <Chip tone="success" onClick={onClick}>
          Live
        </Chip>
      </>,
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const chip = screen.getByRole("button", { name: "Live" });
    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);

    for (const key of [" ", "Enter"]) {
      fireEvent.keyDown(chip, { key });
      fireEvent.keyUp(chip, { key });
    }
    expect(heard).not.toHaveBeenCalled();
    fireEvent.keyDown(chip, { key: " ", ctrlKey: true });
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
  });

  it("toggle independently and not while disabled", () => {
    function Row() {
      const [on, setOn] = useState({ video: false, live: false });
      return (
        <>
          <ToggleChip checked={on.video} onChange={(video) => setOn({ ...on, video })}>
            Video
          </ToggleChip>
          <ToggleChip checked={on.live} onChange={(live) => setOn({ ...on, live })}>
            Live
          </ToggleChip>
          <ToggleChip checked={false} disabled onChange={() => on}>
            Off
          </ToggleChip>
        </>
      );
    }
    render(<Row />);
    const [video, live, off] = screen.getAllByRole("checkbox");
    expect([video, live].map((box) => (box as HTMLInputElement).checked)).toEqual([false, false]);
    fireEvent.click(video!);
    fireEvent.click(live!);
    fireEvent.click(video!);
    expect([video, live].map((box) => (box as HTMLInputElement).checked)).toEqual([false, true]);
    expect((off as HTMLInputElement).disabled).toBe(true);
  });
});

const chosen = (name: string) => (screen.getByRole("radio", { name }) as HTMLInputElement).checked;

describe.each([
  ["choice chips", ChoiceChips],
  ["segmented control", SegmentedControl],
])("%s", (_name, Choice) => {
  const options = [
    ["https", "HTTPS"],
    ["http", "HTTP"],
  ] as const;

  it("keep exactly one chosen, and choosing the current one does nothing", () => {
    const onChange = vi.fn();
    render(<Choice label="Protocol" options={options} value="http" onChange={onChange} />);
    expect(screen.getByRole("radiogroup", { name: "Protocol" })).toBeTruthy();
    expect(chosen("HTTP")).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.getAllByRole("radio").filter((radio) => (radio as HTMLInputElement).checked),
    ).toHaveLength(1);

    fireEvent.click(screen.getByRole("radio", { name: "HTTP" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("radio", { name: "HTTPS" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("https");
  });

  it("choose the first option for an unknown value, and keep arrow keys from the shortcuts", () => {
    const { heard, stop } = shortcuts();
    const onChange = vi.fn();
    const { rerender } = render(
      <Choice label="Protocol" options={options} value={"none" as "http"} onChange={onChange} />,
    );
    expect(onChange).toHaveBeenCalledExactlyOnceWith("https");
    rerender(
      <Choice label="Protocol" options={options} value={"none" as "http"} onChange={onChange} />,
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const first = screen.getByRole("radio", { name: "HTTPS" });
    expect(chosen("HTTPS")).toBe(true);
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(heard).not.toHaveBeenCalled();
    stop();
  });
});
