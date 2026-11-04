import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { SettingsDialog } from "./SettingsDialog";

const row = vi.hoisted(() => (key: string, kind: object, over: object) => ({
  key,
  kind,
  label: key,
  description: null,
  page: "General",
  section: "Appearance",
  live: true,
  env: false,
  secret: false,
  scope: "personal",
  ...over,
}));

// A table with every kind of setting, so the dialog is checked against what it is built from, not today's rows.
vi.mock(import("../generated/settings"), async (original) => ({
  ...(await original()),
  SETTINGS: [
    row("t.flag", { type: "bool", default: false }, { label: "Flag", description: "Flags it." }),
    row("t.hidden", { type: "bool", default: false }, { page: null }),
    row(
      "t.size",
      { type: "auto_or_whole" },
      { label: "Size", page: "Network", section: "Memory", live: false, scope: "server" },
    ),
    row(
      "t.mode",
      {
        type: "choice",
        options: [
          ["https", "HTTPS"],
          ["http", "HTTP"],
        ],
        default: "https",
        toggle: true,
      },
      { label: "Mode", page: "Network", section: "Memory", scope: "server" },
    ),
    row(
      "t.port",
      { type: "whole" },
      { label: "Port", page: "Network", section: "Memory", env: true, scope: "server" },
    ),
    row(
      "t.dir",
      { type: "folder" },
      { label: "Dir", page: "Network", section: "Folders", live: false, scope: "server" },
    ),
    row(
      "scan.logins",
      { type: "records", fields: ["user", "password"], default: [] },
      {
        label: "Logins",
        page: "Network",
        section: "Sign-in",
        env: true,
        secret: true,
        scope: "server",
      },
    ),
    row(
      "t.token",
      { type: "text", default: "" },
      {
        label: "Token",
        page: "Network",
        section: "Credentials",
        env: true,
        secret: true,
        scope: "server",
      },
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
    const values = {
      "t.flag": false,
      "t.hidden": true,
      "t.size": "auto",
      "t.mode": "https",
      "t.port": 554,
      "t.dir": "",
      "t.token": "hunter2",
      "scan.logins": [],
    };
    const state = { ...DEFAULT_SETTINGS, values } as never;
    render(
      <GrantsContext.Provider value={["configuration"]}>
        <FakeSettings state={state} save={save}>
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </GrantsContext.Provider>,
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "General",
      "Network",
    ]);
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeTruthy();
    expect(screen.getByLabelText("Flag").closest("[title]")?.getAttribute("title")).toBe(
      "Flags it.",
    );
    expect(screen.queryByText("t.hidden")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Network" }));
    const token = screen.getByLabelText("Token") as HTMLInputElement;
    // A control's tooltip is its description, else its key.
    expect(token.closest("[title]")?.getAttribute("title")).toBe("t.token");
    expect(token.type).toBe("password");
    const warning = screen.getByText(/^Saved as plain text/);
    expect(warning.textContent).toBe(
      "Saved as plain text. Use $VARIABLE to read it from the environment.",
    );
    expect(token.getAttribute("aria-describedby")).toBe(warning.id);
    fireEvent.change(token, { target: { value: "$TOKEN" } });
    expect(token.type).toBe("text");
    expect(screen.queryByText(/^Saved as plain text/)).toBeNull();
    expect(token.getAttribute("aria-describedby")).toBeNull();
    expect(screen.queryByRole("button", { name: "Choose Dir" })).toBeNull();
    expect(
      within(screen.getByRole("tabpanel")).getAllByTitle("Applies after a restart"),
    ).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("Size"), { target: { value: "512" } });
    fireEvent.click(
      within(screen.getByRole("radiogroup", { name: "Mode" })).getByLabelText("HTTP"),
    );
    fireEvent.change(screen.getByLabelText("Port"), { target: { value: "$PORT" } });
    fireEvent.change(screen.getByLabelText("Dir"), { target: { value: "/data/cache" } });

    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(save).toHaveBeenCalledWith({
      values: {
        ...values,
        "t.mode": "http",
        "t.size": 512,
        "t.port": "$PORT",
        "t.dir": "/data/cache",
        "t.token": "$TOKEN",
      },
      shortcuts: DEFAULT_SETTINGS.shortcuts,
    });
  });

  it("offers the Server rows only to someone who may configure the server", () => {
    render(
      <FakeSettings>
        <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
      </FakeSettings>,
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["General"]);
  });

  it("fills a folder field from the native folder dialog", async () => {
    const pickFolder = vi.fn(() => Promise.resolve("/mnt/cache"));
    window.axDevilWindow = { pickFolder } as never;
    const save = vi.fn(async () => null);
    const values = { ...DEFAULT_SETTINGS.values, "t.dir": "" };
    render(
      <GrantsContext.Provider value={["configuration"]}>
        <FakeSettings state={{ ...DEFAULT_SETTINGS, values } as never} save={save}>
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </GrantsContext.Provider>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Network" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose Dir" }));
    expect(pickFolder).toHaveBeenCalledWith("Dir");
    await waitFor(() =>
      expect((screen.getByLabelText("Dir") as HTMLInputElement).value).toBe("/mnt/cache"),
    );
    delete window.axDevilWindow;
  });

  describe("scan logins", () => {
    const logins = [{ user: "root", password: "********" }];
    const open = (save: () => Promise<null>, onClose = () => {}) => {
      const values = { ...DEFAULT_SETTINGS.values, "scan.logins": logins };
      render(
        <GrantsContext.Provider value={["configuration"]}>
          <FakeSettings state={{ ...DEFAULT_SETTINGS, values } as never} save={save}>
            <SettingsDialog onClose={onClose} onRestartNeeded={() => {}} />
          </FakeSettings>
        </GrantsContext.Provider>,
      );
      fireEvent.click(screen.getByRole("tab", { name: "Network" }));
    };

    it("are staged in the dialog and saved with it on OK", () => {
      const save = vi.fn(async () => null);
      open(save);
      fireEvent.click(screen.getByText("Add login"));
      fireEvent.change(screen.getAllByLabelText("Username")[1]!, { target: { value: " ops " } });
      fireEvent.change(screen.getAllByLabelText("Password")[1]!, { target: { value: "$OPS" } });
      expect(save).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "OK" }));
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          values: expect.objectContaining({
            "scan.logins": [...logins, { user: "ops", password: "$OPS" }],
          }),
        }),
      );
    });

    it("are discarded on Cancel", () => {
      const save = vi.fn(async () => null);
      const onClose = vi.fn();
      open(save, onClose);
      fireEvent.click(screen.getByLabelText("Remove login"));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onClose).toHaveBeenCalled();
      expect(save).not.toHaveBeenCalled();
    });
  });
});
