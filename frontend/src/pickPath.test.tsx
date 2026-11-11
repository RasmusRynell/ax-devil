import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AccessContext } from "./access";
import { PathPickerProvider, usePickPath, VIDEO } from "./pickPath";

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
    window.axDevilWindow = { disconnect: () => Promise.resolve() } as unknown as typeof window.axDevilWindow;
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
