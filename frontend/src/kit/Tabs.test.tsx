import { useState } from "react";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Tabs } from "./Tabs";

afterEach(cleanup);

const TABS = ["Look", "Filter", "Timing"] as const;

function Harness() {
  const [value, setValue] = useState<(typeof TABS)[number]>("Look");
  return <Tabs label="Lane" tabs={TABS} value={value} onChange={setValue} panelId="panel" />;
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
});
