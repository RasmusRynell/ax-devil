import { useContext } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "./access";
import { DEFAULT_SETTINGS } from "./fakeSettings";
import { SettingsNoticeContext, SettingsProvider, useSettings } from "./settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const noInfo = () => {};

function Catalog() {
  const { values, set } = useSettings();
  return (
    <button onClick={() => void set("overlays.default_catalog", "user:night.json")}>
      {(values["overlays.default_catalog"] as string) || "built-in"}
    </button>
  );
}

function TextSize() {
  const { set } = useSettings();
  return <button onClick={() => void set("ui.text_size", "large")}>Larger text</button>;
}

describe("settings", () => {
  it("applies the theme and text size to the document root as they are saved", async () => {
    render(
      <SettingsProvider onInfo={noInfo}>
        <TextSize />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Larger text" }));
    await waitFor(() => expect(document.documentElement.dataset.textSize).toBe("large"));
    expect(document.documentElement.dataset.theme).toBe("auto");
  });

  it("keeps the Personal default catalog of a browser without a backend for it in the browser", async () => {
    render(
      <SettingsProvider onInfo={noInfo}>
        <Catalog />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "built-in" }));
    await waitFor(() => screen.getByRole("button", { name: "user:night.json" }));
    expect(JSON.parse(window.localStorage.getItem("ax-devil.preferences") ?? "{}")).toMatchObject({
      values: { "overlays.default_catalog": "user:night.json" },
    });
    cleanup();
    render(
      <SettingsProvider onInfo={noInfo}>
        <Catalog />
      </SettingsProvider>,
    );
    expect(screen.getByRole("button", { name: "user:night.json" })).toBeTruthy();
  });

  /** A notice of `revision`, as the session connection reports it, and the way to save a folder. */
  function Notices() {
    const noticed = useContext(SettingsNoticeContext);
    const { set, values } = useSettings();
    return (
      <>
        <button onClick={() => noticed(5)}>Notice</button>
        <button onClick={() => void set("storage.cache_dir", "/saved")}>Save</button>
        <p>{`folder ${values["storage.cache_dir"] as string}`}</p>
      </>
    );
  }
  const asOwner = (info = noInfo) =>
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
        <SettingsProvider onInfo={info}>
          <Notices />
        </SettingsProvider>
      </AccessContext.Provider>,
    );

  it("keeps the app and the settings already read when a read after a notice fails", async () => {
    const answers = [
      { ok: true, json: async () => DEFAULT_SETTINGS },
      { ok: false, status: 503, json: async () => null },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/info" ? { ok: false, status: 503, json: async () => null } : answers.shift(),
      ),
    );
    asOwner();
    fireEvent.click(await screen.findByRole("button", { name: "Notice" }));
    await waitFor(() => expect(answers).toHaveLength(0));
    expect(screen.getByText(/^folder/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("drops a read that a later save overtook, and hands the info to the app", async () => {
    const saved = {
      ...DEFAULT_SETTINGS,
      revision: 6,
      values: { ...DEFAULT_SETTINGS.values, "storage.cache_dir": "/saved" },
    };
    let answerRead: (state: unknown) => void = () => {};
    const read = new Promise((resolve) => {
      answerRead = resolve;
    });
    const answers = [Promise.resolve(DEFAULT_SETTINGS), read, Promise.resolve(saved)];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          url === "/api/info" ? { owner: true, fullAccess: true } : answers.shift(),
      })),
    );
    const info = vi.fn();
    asOwner(info);
    fireEvent.click(await screen.findByRole("button", { name: "Notice" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("folder /saved");
    expect(info).toHaveBeenCalledWith({ owner: true, fullAccess: true });
    answerRead({ ...DEFAULT_SETTINGS, revision: 5 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("folder /saved")).toBeTruthy();
  });
});
