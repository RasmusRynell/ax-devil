import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Popover } from "./Popover";

afterEach(cleanup);

describe("popover", () => {
  it("closes on a click outside without passing that click on", () => {
    const beneath = vi.fn();
    render(
      <>
        <Popover label="Appearance" trigger="Appearance">
          <button type="button">Inside</button>
        </Popover>
        <button type="button" onClick={beneath}>
          Beneath
        </button>
      </>,
    );
    const click = (name: string) => {
      const target = screen.getByRole("button", { name });
      fireEvent.pointerDown(target);
      fireEvent.pointerUp(target);
      fireEvent.click(target);
    };
    click("Appearance");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Inside" }));
    click("Inside");
    expect(screen.queryByRole("dialog")).not.toBeNull();

    click("Beneath");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(beneath).not.toHaveBeenCalled();

    click("Beneath");
    expect(beneath).toHaveBeenCalledTimes(1);
  });
});
