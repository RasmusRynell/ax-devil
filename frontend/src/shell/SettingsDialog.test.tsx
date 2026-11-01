import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { SettingsDialog } from "./SettingsDialog";

const row = vi.hoisted(() => (key: string, kind: object, over: object) => ({
  key,
  kind,
  label: key,
  page: "General",
  section: "Appearance",
  live: true,
  env: false,
  secret: false,
  ...over,
}));

// A table with every kind of setting, so the dialog is checked against what it is built from, not today's rows.
vi.mock(import("../generated/settings"), async (original) => ({
  ...(await original()),
  SETTINGS: [
    row("t.flag", { type: "bool", default: false }, { label: "Flag" }),
    row("t.hidden", { type: "bool", default: false }, { page: null }),
    row(
      "t.size",
      { type: "auto_or_whole" },
      { label: "Size", page: "Network", section: "Memory", live: false },
    ),
    row(
      "t.token",
      { type: "text", default: "" },
      { label: "Token", page: "Network", section: "Credentials", env: true, secret: true },
    ),
  ] as never,
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(cleanup);
afterAll(() => vi.restoreAllMocks());

describe("settings dialog", () => {
  it("is built from the table: pages, sections, a control per kind, masked secrets, restart marker", () => {
    const save = vi.fn(async () => null);
    const values = { "t.flag": false, "t.hidden": true, "t.size": "auto", "t.token": "hunter2" };
    const state = { ...DEFAULT_SETTINGS, values } as never;
    render(
      <FakeSettings state={state} save={save}>
        <SettingsDialog onClose={() => {}} />
      </FakeSettings>,
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "General",
      "Network",
    ]);
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeTruthy();
    expect(screen.getByLabelText("Flag")).toBeTruthy();
    expect(screen.queryByText("t.hidden")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Network" }));
    const token = screen.getByLabelText("Token") as HTMLInputElement;
    expect(token.type).toBe("password");
    fireEvent.change(token, { target: { value: "$TOKEN" } });
    expect(token.type).toBe("text");
    const size = screen.getByLabelText("Size");
    expect(
      within(size.closest("label") as HTMLElement).getByTitle("Applies after a restart"),
    ).toBeTruthy();
    fireEvent.change(size, { target: { value: "512" } });

    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(save).toHaveBeenCalledWith({
      values: { ...values, "t.size": 512, "t.token": "$TOKEN" },
      shortcuts: DEFAULT_SETTINGS.shortcuts,
    });
  });
});
