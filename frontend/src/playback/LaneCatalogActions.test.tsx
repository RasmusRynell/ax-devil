import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AccessContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { CatalogInfoWire } from "../generated/api";
import { LaneCatalogActions } from "./LaneCatalogActions";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const CATALOGS: CatalogInfoWire[] = [
  { id: "built-in:standard", name: "Standard", description: "" },
  { id: "built-in:tracking", name: "Tracking", description: "" },
  { id: "user:night.json", name: "Standard", description: "" },
  { id: "user:broken.json", name: "broken", description: "", problem: "not valid JSON" },
];

/** A backend whose catalog routes answer with `reply`; the calls it got are the returned list. */
function routes(reply: { ok: true; body?: unknown } | { ok: false; reason: string }) {
  const calls: [string, unknown][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, JSON.parse(init?.body as string)]);
      return reply.ok
        ? { ok: true, status: reply.body === undefined ? 204 : 200, json: async () => reply.body }
        : {
            ok: false,
            status: 400,
            json: async () => ({ code: "CatalogInvalid", params: {}, reason: reply.reason }),
          };
    }),
  );
  return calls;
}

/** `defaultId` is the catalog new views start with; `stored` is the Personal setting, which differs when it names a catalog that cannot be used. */
function setup(
  catalog: string,
  defaultId: string,
  access = { owner: true, fullAccess: false },
  stored = defaultId,
) {
  const onChange = vi.fn();
  const save = vi.fn(async () => null);
  const reload = vi.fn(async () => {});
  const onReload = vi.fn();
  const view = (current: string, listing: CatalogInfoWire[] = CATALOGS) => (
    <AccessContext value={access}>
      <FakeSettings
        state={{
          ...DEFAULT_SETTINGS,
          values: { ...DEFAULT_SETTINGS.values, "overlays.default_catalog": stored },
        }}
        save={save}
        reload={reload}
      >
        <LaneCatalogActions
          catalog={current}
          label={current === "user:night.json" ? "Standard (night.json)" : "Standard"}
          catalogs={listing}
          defaultId={defaultId}
          revision={7}
          onReload={onReload}
          onChange={onChange}
        />
      </FakeSettings>
    </AccessContext>
  );
  const { rerender } = render(view(catalog));
  return {
    onChange,
    save,
    reload,
    onReload,
    moveTo: (next: string) => rerender(view(next)),
    list: (listing: CatalogInfoWire[]) => rerender(view(catalog, listing)),
  };
}

const confirmDelete = () =>
  fireEvent.click(
    within(screen.getByRole("dialog", { name: "Delete Catalog" })).getByRole("button", {
      name: "Delete",
    }),
  );

describe("catalog copy and delete", () => {
  it("need an Owner", () => {
    setup("user:night.json", "built-in:standard", { owner: false, fullAccess: false });
    expect(screen.getByRole("button", { name: "Use as default" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("copies the catalog under the name typed and switches the lane to the copy once it is listed", async () => {
    const calls = routes({ ok: true, body: { id: "user:mine.json" } });
    const { onChange, list } = setup("built-in:standard", "built-in:standard");
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    const name = screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;
    expect(name.value).toBe("Standard copy");
    expect([name.selectionStart, name.selectionEnd]).toEqual([0, name.value.length]);
    const create = screen.getByRole("button", { name: "Create" }) as HTMLButtonElement;
    fireEvent.change(name, { target: { value: "  " } });
    expect(create.disabled).toBe(true);
    fireEvent.change(name, { target: { value: " Mine " } });
    fireEvent.click(create);
    expect(create.disabled).toBe(true);
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => {});
    expect(calls).toEqual([
      ["/api/catalogs/copy", { source: "built-in:standard", revision: 7, name: "Mine" }],
    ]);
    expect(onChange).not.toHaveBeenCalled();
    expect(create.disabled).toBe(true);
    list([...CATALOGS, { id: "user:mine.json", name: "Mine", description: "" }]);
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledExactlyOnceWith({ catalog: "user:mine.json", theme: null }),
    );
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  });

  it("makes the backend read the file again, then asks for the document anew", async () => {
    const calls = routes({ ok: true });
    const { onReload } = setup("user:night.json", "built-in:standard");
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
    expect(calls).toEqual([["/api/catalogs/reload", { id: "user:night.json" }]]);
  });

  it("says why a copy was refused and keeps the name for another try", async () => {
    routes({ ok: false, reason: "that name is taken" });
    const { onChange } = setup("built-in:standard", "built-in:standard");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Could not create the catalog: that name is taken",
      ),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(
      "Standard copy",
    );
  });

  it("leaves the lane on the catalog picked while a copy was still being made", async () => {
    let answer: (response: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => (answer = resolve))),
    );
    const { onChange, moveTo, list } = setup("built-in:standard", "built-in:standard");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    moveTo("built-in:tracking");
    await act(async () =>
      answer({ ok: true, status: 200, json: async () => ({ id: "user:mine.json" }) }),
    );
    list([...CATALOGS, { id: "user:mine.json", name: "Mine", description: "" }]);
    await act(async () => {});
    expect(onChange).not.toHaveBeenCalled();
  });

  it("closes an open copy form when the lane moves to another catalog", () => {
    const { moveTo } = setup("built-in:standard", "built-in:standard");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(screen.getByRole("textbox", { name: "Name" })).toBeTruthy();
    moveTo("built-in:tracking");
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });

  it("deletes a user catalog after asking, reloads the settings the backend changed and moves the lane to Standard", async () => {
    const calls = routes({ ok: true });
    const { onChange, save, reload } = setup("user:night.json", "user:night.json");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog", { name: "Delete Catalog" }).textContent).toContain(
      "Delete Standard (night.json)? The file is deleted permanently. It is the default catalog, so new views will start from Standard.",
    );
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).toEqual([]);
    expect(screen.queryByRole("dialog", { name: "Delete Catalog" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    confirmDelete();
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledExactlyOnceWith({
        catalog: "built-in:standard",
        theme: null,
      }),
    );
    expect(calls).toEqual([["/api/catalogs/delete", { id: "user:night.json" }]]);
    expect(reload).toHaveBeenCalledOnce();
    expect(save).not.toHaveBeenCalled();
  });

  it("leaves the lane on the catalog picked while a delete was still running", async () => {
    let answer: (response: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => (answer = resolve))),
    );
    const { onChange, moveTo } = setup("user:night.json", "user:night.json");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    confirmDelete();
    moveTo("built-in:tracking");
    await act(async () => answer({ ok: true, status: 204, json: async () => undefined }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("leaves the default alone when another catalog is deleted, and a refused delete changes nothing", async () => {
    routes({ ok: true });
    const { onChange, save } = setup("user:night.json", "built-in:tracking");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog").textContent).not.toContain("default");
    confirmDelete();
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledExactlyOnceWith({
        catalog: "built-in:tracking",
        theme: null,
      }),
    );
    expect(save).not.toHaveBeenCalled();
    cleanup();
    routes({ ok: false, reason: "the file is in use" });
    const refused = setup("user:night.json", "built-in:standard");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    confirmDelete();
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Could not delete the catalog: the file is in use",
      ),
    );
    expect(refused.onChange).not.toHaveBeenCalled();
  });

  it("lets Standard be saved as the default while the stored one names a catalog that is gone", () => {
    const { save } = setup(
      "built-in:standard",
      "built-in:standard",
      { owner: true, fullAccess: false },
      "user:gone.json",
    );
    fireEvent.click(screen.getByRole("button", { name: "Use as default" }));
    expect(save).toHaveBeenCalledWith({
      values: { ...DEFAULT_SETTINGS.values, "overlays.default_catalog": "built-in:standard" },
      shortcuts: DEFAULT_SETTINGS.shortcuts,
    });
  });
});
