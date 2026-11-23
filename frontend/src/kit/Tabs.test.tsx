import { useState } from "react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Tabs } from "./Tabs";

afterEach(cleanup);

const TABS = ["Look", "Filter", "Timing"] as const;

function Harness({ orientation }: { orientation?: "vertical" }) {
  const [value, setValue] = useState<(typeof TABS)[number]>("Look");
  return (
    <Tabs
      label="Lane"
      tabs={TABS}
      value={value}
      onChange={setValue}
      panelId="panel"
      orientation={orientation}
    />
  );
}

describe("tabs", () => {
  it("moves the selection and focus with the arrow keys, wrapping at the ends", () => {
    render(<Harness />);
    const selected = () => screen.getByRole("tab", { selected: true });
    expect(selected().textContent).toBe("Look");
    fireEvent.keyDown(selected(), { key: "ArrowRight" });
    expect(selected().textContent).toBe("Filter");
    expect(document.activeElement).toBe(selected());
    expect(screen.getByRole("tab", { name: "Look" }).getAttribute("tabindex")).toBe("-1");
    fireEvent.keyDown(selected(), { key: "ArrowLeft" });
    fireEvent.keyDown(selected(), { key: "ArrowLeft" });
    expect(selected().textContent).toBe("Timing");
  });

  it("moves with the up and down arrow keys only when it is vertical", () => {
    const { rerender } = render(<Harness />);
    const selected = () => screen.getByRole("tab", { selected: true });
    fireEvent.keyDown(selected(), { key: "ArrowDown" });
    expect(selected().textContent).toBe("Look");
    rerender(<Harness orientation="vertical" />);
    fireEvent.keyDown(selected(), { key: "ArrowDown" });
    expect(selected().textContent).toBe("Filter");
    fireEvent.keyDown(selected(), { key: "ArrowUp" });
    expect(selected().textContent).toBe("Look");
  });

  it("keeps its own keys from the shell's shortcuts, but not Ctrl combinations", () => {
    render(<Harness />);
    const seen: string[] = [];
    const listen = (event: KeyboardEvent) => seen.push(event.key);
    document.addEventListener("keydown", listen);
    fireEvent.keyDown(screen.getByRole("tab", { selected: true }), { key: "ArrowRight" });
    expect(seen).toEqual([]);
    fireEvent.keyDown(screen.getByRole("tab", { selected: true }), {
      key: "ArrowRight",
      ctrlKey: true,
    });
    document.removeEventListener("keydown", listen);
    expect(seen).toEqual(["ArrowRight"]);
  });
});
