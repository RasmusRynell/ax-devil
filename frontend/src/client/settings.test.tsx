import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { deviceStore } from "./deviceSettings";
import { SettingsProvider, useSettings } from "./settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

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
      <SettingsProvider>
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
      <SettingsProvider>
        <Catalog />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "built-in" }));
    await screen.findByText("refused");
    expect(window.localStorage.getItem("ax-devil.preferences")).toBeNull();
  });
});
