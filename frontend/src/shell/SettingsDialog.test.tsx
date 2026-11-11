import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AccessContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { SettingsContext, type SettingsApi } from "../settings";
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
    row(
      "t.flag",
      { type: "bool", default: false },
      { label: "Flag", description: "Flags it.", scope: "device" },
    ),
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
      "t.decoder",
      { type: "decoder", default: "A" },
      { label: "Decoder", page: "Network", section: "Credentials", scope: "server" },
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
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
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
      "t.token": { state: "literal" },
      "scan.logins": [],
    };
    const state = { ...DEFAULT_SETTINGS, values } as never;
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
        <FakeSettings state={state} apply={save}>
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </AccessContext.Provider>,
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
    expect([token.value, token.placeholder]).toEqual(["", "••••••••"]);
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
    // Only what the dialog changed is sent: the flag it left alone is not.
    expect(save).toHaveBeenCalledWith({
      set: {
        "t.mode": "http",
        "t.size": 512,
        "t.port": "$PORT",
        "t.dir": "/data/cache",
        "t.token": "$TOKEN",
      },
    });
  });

  it("offers the installed live decoders for a decoder row and saves any text typed there", async () => {
    const decoder = (handler: string, display_name: string, live: boolean) => ({
      handler,
      display_name,
      extensions: [],
      options: [],
      live,
    });
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        decoders: [decoder("ADF_V1_FRAME", "ADF v1 frame", true), decoder("MOT", "MOT", false)],
        resolvers: [],
      }),
    }));
    const save = vi.fn(async () => null);
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
        <FakeSettings
          state={
            {
              ...DEFAULT_SETTINGS,
              values: { ...DEFAULT_SETTINGS.values, "t.decoder": "A" },
            } as never
          }
          apply={save}
        >
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </AccessContext.Provider>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Network" }));
    const field = screen.getByLabelText("Decoder") as HTMLInputElement;
    await waitFor(() => expect(field.list?.options).toHaveLength(1));
    expect(field.list?.options[0]).toMatchObject({ value: "ADF_V1_FRAME", label: "ADF v1 frame" });
    fireEvent.change(field, { target: { value: "MY_PLUGIN_DECODER" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(save).toHaveBeenCalledWith({ set: { "t.decoder": "MY_PLUGIN_DECODER" } });
  });

  it("offers a page that holds only a join link the Device rows alone", () => {
    render(
      <FakeSettings>
        <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
      </FakeSettings>,
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["General"]);
  });

  it("names the config file and its version on an Advanced page, for a backend that tells them", () => {
    render(
      <FakeSettings
        state={{ ...DEFAULT_SETTINGS, file: { path: "/home/me/settings.json", version: 3 } }}
      >
        <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
      </FakeSettings>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Advanced" }));
    expect(screen.getByText("/home/me/settings.json")).toBeTruthy();
    expect(screen.getByText("Configuration version").lastElementChild?.textContent).toBe("3");
  });

  it("fills a folder field from the native folder dialog", async () => {
    const pickFolder = vi.fn(() => Promise.resolve("/mnt/cache"));
    window.axDevilWindow = { pickFolder } as never;
    const save = vi.fn(async () => null);
    const values = { ...DEFAULT_SETTINGS.values, "t.dir": "" };
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
        <FakeSettings state={{ ...DEFAULT_SETTINGS, values } as never} apply={save}>
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </AccessContext.Provider>,
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
    const logins = [{ id: "a1", user: "root", password: { state: "literal" } }];
    const open = (save: () => Promise<null>, onClose = () => {}) => {
      const values = { ...DEFAULT_SETTINGS.values, "scan.logins": logins };
      render(
        <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
          <FakeSettings state={{ ...DEFAULT_SETTINGS, values } as never} apply={save}>
            <SettingsDialog onClose={onClose} onRestartNeeded={() => {}} />
          </FakeSettings>
        </AccessContext.Provider>,
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
      expect(save).toHaveBeenCalledWith({
        set: { "scan.logins": [...logins, { user: "ops", password: "$OPS" }] },
      });
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

  describe("keyboard shortcuts", () => {
    /** Settings over `apply`, which answers once `until` settles. */
    const open = (apply: SettingsApi["apply"], onClose = vi.fn()) =>
      render(
        <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
          <FakeSettings apply={apply}>
            <SettingsDialog onClose={onClose} onRestartNeeded={() => {}} />
          </FakeSettings>
        </AccessContext.Provider>,
      );
    const clearExit = () => {
      fireEvent.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));
      fireEvent.click(screen.getByRole("button", { name: "Clear Exit" }));
      return screen.getByRole("dialog", { name: "Keyboard Shortcuts" });
    };

    it("are stored by their editor alone, and Settings then sends only what was staged in it", async () => {
      const save = vi.fn(async () => null);
      const onClose = vi.fn();
      open(save, onClose);
      fireEvent.click(screen.getByLabelText("Flag"));
      fireEvent.click(within(clearExit()).getByRole("button", { name: "OK" }));
      await act(async () => {});
      expect(save).toHaveBeenCalledTimes(1);
      expect(save).toHaveBeenLastCalledWith({ shortcutsSet: { exit: null } });
      expect(screen.queryByRole("dialog", { name: "Keyboard Shortcuts" })).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "OK" }));
      expect(save).toHaveBeenLastCalledWith({ set: { "t.flag": true } });
    });

    it("stay stored when Settings is cancelled", async () => {
      const save = vi.fn(async () => null);
      const onClose = vi.fn();
      open(save, onClose);
      fireEvent.click(within(clearExit()).getByRole("button", { name: "OK" }));
      await act(async () => {});
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onClose).toHaveBeenCalled();
      expect(save).toHaveBeenCalledTimes(1);
    });

    it("cannot be opened while Settings is saving, and their editor cannot be dismissed while saving", async () => {
      let answer: (refused: null) => void = () => {};
      open(vi.fn(() => new Promise<null>((resolve) => (answer = resolve))));
      fireEvent.click(screen.getByLabelText("Flag"));
      fireEvent.click(screen.getByRole("button", { name: "OK" }));
      expect(
        (screen.getByRole("button", { name: "Keyboard shortcuts" }) as HTMLButtonElement).disabled,
      ).toBe(true);
      await act(async () => answer(null));
    });

    it("have an editor that cannot be dismissed while its write is pending", async () => {
      let answer: (refused: null) => void = () => {};
      open(vi.fn(() => new Promise<null>((resolve) => (answer = resolve))));
      const editor = clearExit();
      fireEvent.click(within(editor).getByRole("button", { name: "OK" }));
      fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
      fireEvent(editor, new Event("cancel", { cancelable: true }));
      expect(screen.getByRole("dialog", { name: "Keyboard Shortcuts" })).toBe(editor);

      await act(async () => answer(null));
      expect(screen.queryByRole("dialog", { name: "Keyboard Shortcuts" })).toBeNull();
    });
  });
});
