import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, render, screen } from "@testing-library/react";
import { MiddleElided } from "./MiddleElided";

afterEach(cleanup);

describe("middle elided text", () => {
  it("keeps the last 8 characters apart from what may be cut, and the whole text as the title", () => {
    render(<MiddleElided text="vehicle-0123456789-abcdef01" />);
    expect(screen.getByText("abcdef01")).toBeTruthy();
    expect(screen.getByTitle("vehicle-0123456789-abcdef01").textContent).toBe(
      "vehicle-0123456789-abcdef01",
    );
  });

  it("leaves an id of 16 characters or fewer in one piece", () => {
    render(<MiddleElided text="0123456789abcdef" />);
    expect(screen.getByText("0123456789abcdef")).toBeTruthy();
  });
});
