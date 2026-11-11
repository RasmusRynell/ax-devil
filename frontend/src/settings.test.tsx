import { useContext, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "./access";
import { deviceStore } from "./deviceSettings";
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
  const [refused, setRefused] = useState(false);
  return (
    <>
      <button
        onClick={() =>
          void set("overlays.default_catalog", "user:night.json").then(
            (refusal) => refusal && setRefused(true),
          )
        }
      >
        {(values["overlays.default_catalog"] as string) || "built-in"}
      </button>
      {refused && <p>refused</p>}
    </>
  );
}

function TextSize() {
  const { set } = useSettings();
  return <button onClick={() => void set("ui.text_size", "large")}>Larger text</button>;
}

describe("settings", () => {
  it("stores a Device setting on the device, applies it and sends no request, even for a Guest", async () => {
    const fetched = vi.fn();
    vi.stubGlobal("fetch", fetched);
    render(
      <SettingsProvider onInfo={noInfo}>
        <TextSize />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Larger text" }));
    await waitFor(() => expect(document.documentElement.dataset.textSize).toBe("large"));
    expect(JSON.parse(window.localStorage.getItem("ax-devil.device") ?? "{}")).toEqual({
      "ui.text_size": "large",
    });
    expect(fetched).not.toHaveBeenCalled();
    deviceStore.reset("ui.text_size");
  });

  it("gives a Guest the default of every Personal setting, refuses its change and drops the old copy", async () => {
    window.localStorage.setItem(
      "ax-devil.preferences",
      JSON.stringify({ values: { "overlays.default_catalog": "user:night.json" } }),
    );
    render(
      <SettingsProvider onInfo={noInfo}>
        <Catalog />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "built-in" }));
    await screen.findByText("refused");
    expect(window.localStorage.getItem("ax-devil.preferences")).toBeNull();
  });

  /** A notice of `revision`, as the session connection reports it, and the way to save a folder. */
  function Notices() {
    const noticed = useContext(SettingsNoticeContext);
    const { set, values } = useSettings();
    return (
      <>
        <button onClick={() => noticed(5)}>Notice</button>
        <button onClick={() => noticed(6)}>Newer notice</button>
        <button onClick={() => noticed(2, true)}>Reconnected</button>
        <button onClick={() => noticed(9, true)}>Reconnected at the same revision</button>
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

  it("takes the first notice after a reconnect even when its revision is lower", async () => {
    const at = (revision: number, folder: string) => ({
      ...DEFAULT_SETTINGS,
      revision,
      values: { ...DEFAULT_SETTINGS.values, "storage.cache_dir": folder },
    });
    const answers = [at(9, "/before"), at(2, "/after")];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url === "/api/info" ? { owner: true } : answers.shift()),
      })),
    );
    asOwner();
    await screen.findByText("folder /before");
    fireEvent.click(screen.getByRole("button", { name: "Reconnected" }));
    await screen.findByText("folder /after");
  });

  it("fetches nothing on a fresh notice whose revision is the one already held", async () => {
    const fetched = vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url === "/api/info" ? { owner: true } : { ...DEFAULT_SETTINGS, revision: 9 },
    }));
    vi.stubGlobal("fetch", fetched);
    asOwner();
    await screen.findByText(/^folder/);
    const asked = fetched.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Reconnected at the same revision" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetched).toHaveBeenCalledTimes(asked);
  });

  it("drops an info reply that a newer info request overtook", async () => {
    const infos: Array<(info: unknown) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/info"
          ? { ok: true, json: () => new Promise((resolve) => infos.push(resolve)) }
          : { ok: true, json: async () => DEFAULT_SETTINGS },
      ),
    );
    const info = vi.fn();
    asOwner(info);
    await screen.findByText(/^folder/);
    fireEvent.click(screen.getByRole("button", { name: "Notice" }));
    fireEvent.click(screen.getByRole("button", { name: "Newer notice" }));
    await waitFor(() => expect(infos).toHaveLength(2));
    infos[1]?.({ fullAccess: true });
    infos[0]?.({ fullAccess: false });
    await waitFor(() => expect(info).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(info).toHaveBeenCalledTimes(1);
    expect(info).toHaveBeenCalledWith({ fullAccess: true });
  });
});
