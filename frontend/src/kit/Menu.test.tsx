import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, render, screen } from "@testing-library/react";
import { Menu } from "./Menu";

afterEach(cleanup);

describe("menu", () => {
  it("tells what an enabled item does in its tooltip, and why a disabled one is off", () => {
    render(
      <Menu
        entries={[
          { kind: "action", id: "hide", label: "Hide", tip: "Skips it" },
          {
            kind: "action",
            id: "export",
            label: "Export",
            disabled: true,
            reason: "Not now",
            tip: "Saves it",
          },
        ]}
        checked={new Set()}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByRole("menuitem", { name: "Hide" }).title).toBe("Skips it");
    expect(screen.getByRole("menuitem", { name: "Export" }).title).toBe("Not now");
  });
});
