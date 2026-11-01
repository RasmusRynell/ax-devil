import { afterEach, describe, expect, it } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsProvider, useSettings } from "./settings";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

function Catalog() {
  const { values, shortcuts, save } = useSettings();
  return (
    <button
      onClick={() =>
        void save({
          values: { ...values, "overlays.default_catalog": "user:night.json" },
          shortcuts,
        })
      }
    >
      {String(values["overlays.default_catalog"]) || "built-in"}
    </button>
  );
}

describe("settings", () => {
  it("keeps the Personal default catalog of a browser without a backend for it in the browser", async () => {
    render(
      <SettingsProvider>
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
      <SettingsProvider>
        <Catalog />
      </SettingsProvider>,
    );
    expect(screen.getByRole("button", { name: "user:night.json" })).toBeTruthy();
  });
});
