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
      {(values["overlays.default_catalog"] as string) || "built-in"}
    </button>
  );
}

function TextSize() {
  const { values, shortcuts, save } = useSettings();
  return (
    <button
      onClick={() => void save({ values: { ...values, "ui.text_size": "large" }, shortcuts })}
    >
      Larger text
    </button>
  );
}

describe("settings", () => {
  it("applies the theme and text size to the document root as they are saved", async () => {
    render(
      <SettingsProvider>
        <TextSize />
      </SettingsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Larger text" }));
    await waitFor(() => expect(document.documentElement.dataset.textSize).toBe("large"));
    expect(document.documentElement.dataset.theme).toBe("auto");
  });

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
