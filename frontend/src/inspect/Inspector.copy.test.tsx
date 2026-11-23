import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vite-plus/test";
import { InspectorLines } from "./InspectorLines";
import { FRESH_MEMORY } from "./fields";

afterEach(cleanup);

it("uses the complete Raw action from its button, keyboard and context menu even when the page has a preview", () => {
  const copyRaw = vi.fn();
  render(
    <InspectorLines
      fields={[
        {
          group: "raw",
          path: ["end_reason"],
          value: { kind: "text", value: "preview" },
          cut: [4, 9000000],
        },
      ]}
      entity="7"
      scope="sample"
      memory={FRESH_MEMORY}
      onMemory={() => {}}
      onCopyRaw={copyRaw}
    />,
  );
  const button = screen.getByRole("button", { name: "Copy Raw" });
  fireEvent.click(button);
  const row = button.closest<HTMLElement>("[data-group]");
  if (!row) throw Error("Raw caption");
  fireEvent.keyDown(row, { key: "c", ctrlKey: true });
  fireEvent.contextMenu(row);
  fireEvent.click(screen.getByRole("menuitem", { name: "Copy Raw" }));
  expect(copyRaw).toHaveBeenCalledTimes(3);
});
