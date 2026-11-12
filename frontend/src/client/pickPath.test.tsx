import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { WindowBridge } from "./windowBridge";
import { PathPicker } from "../kit/PathPicker";
import { AccessContext } from "./access";
import { PathPickerProvider, usePickPath, VIDEO, exportFile } from "./pickPath";

function Opener({ onPicked }: { onPicked: (path: string | null) => void }) {
  const pick = usePickPath();
  return pick ? <button onClick={() => void pick(VIDEO).then(onPicked)}>Browse</button> : null;
}

function show(owner: boolean, onPicked = vi.fn()) {
  render(
    <AccessContext.Provider value={{ owner, fullAccess: false }}>
      <PathPickerProvider>
        <Opener onPicked={onPicked} />
      </PathPickerProvider>
    </AccessContext.Provider>,
  );
  return onPicked;
}

afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
  vi.unstubAllGlobals();
});

describe("picking a path", () => {
  it("uses the shell's own dialog when this window has one", async () => {
    const pickVideo = vi.fn(() => Promise.resolve("/home/me/a.mp4"));
    window.axDevilWindow = { pickVideo } as unknown as typeof window.axDevilWindow;
    const picked = show(true);
    fireEvent.click(screen.getByRole("button", { name: "Browse" }));
    await vi.waitFor(() => expect(picked).toHaveBeenCalledWith("/home/me/a.mp4"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("otherwise opens the path picker for an Owner, and gives the focus back when it is cancelled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(Response.json({ entries: [], places: [] }))),
    );
    window.axDevilWindow = {
      disconnect: () => Promise.resolve(),
    } as unknown as typeof window.axDevilWindow;
    const picked = show(true);
    const browse = screen.getByRole("button", { name: "Browse" });
    browse.focus();
    fireEvent.click(browse);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(picked).toHaveBeenCalledWith(null));
    await vi.waitFor(() => expect(document.activeElement).toBe(browse));
  });

  it("offers nothing to a Guest", () => {
    show(false);
    expect(screen.queryByRole("button", { name: "Browse" })).toBeNull();
  });
});

it("keeps an export folder separate from a display name and builds one safe file in that folder", async () => {
  for (const [name, file] of [
    ["Review/Camera A bookmarks.csv", "Review-Camera A bookmarks.csv"],
    ["Review\\Camera A bookmarks.csv", "Review-Camera A bookmarks.csv"],
    [".. bookmarks.csv", "- bookmarks.csv"],
  ]) {
    const request = exportFile(name, "csv", "/workspaces");
    const native = vi.fn(() => Promise.resolve(null));
    await request.native({ pickExportFile: native } as unknown as WindowBridge);
    expect(native).toHaveBeenCalledWith(name, "csv", "/workspaces");
    const chosen = vi.fn();
    render(
      <PathPicker
        {...request.picker}
        list={(folder) =>
          Promise.resolve({
            path: folder,
            entries: [],
            places: [{ name: "Home", path: "/isolated-home" }],
          })
        }
        exists={() => Promise.resolve(false)}
        onChoose={chosen}
        onCancel={() => {}}
      />,
    );
    await vi.waitFor(() =>
      expect((screen.getByRole("textbox", { name: "Path" }) as HTMLInputElement).value).toBe(
        "/workspaces",
      ),
    );
    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(file);
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save" });
    await vi.waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await vi.waitFor(() => expect(chosen).toHaveBeenCalledWith(`/workspaces/${file}`));
    cleanup();
  }
});
