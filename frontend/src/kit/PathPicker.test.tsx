import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { FolderListing } from "../generated/api";
import { FolderProblem, PathPicker, type ListFolder, type PickMode } from "./PathPicker";

const PLACES = [
  { name: "Home", path: "/home/me" },
  { name: "/", path: "/" },
];
const FOLDERS: Record<string, Pick<FolderListing, "entries" | "parent">> = {
  "/home/me": {
    parent: "/home",
    entries: [
      { name: "clips", folder: true },
      { name: "notes.txt", folder: false },
    ],
  },
  "/home/me/clips": {
    parent: "/home/me",
    entries: [
      { name: "a.mp4", folder: false },
      { name: "b.mp4", folder: false },
      { name: "b.txt", folder: false },
    ],
  },
};

afterEach(() => {
  cleanup();
});

function pick(mode: PickMode, extra: Partial<Parameters<typeof PathPicker>[0]> = {}) {
  // The backend canonicalizes a path, so a trailing slash is the folder itself.
  const source: ListFolder = (asked) => {
    const path = asked && asked.length > 1 ? asked.replace(/\/$/, "") : asked;
    const folder = FOLDERS[path ?? ""];
    if (path === undefined || folder)
      return Promise.resolve({
        ...(folder && { path, ...folder }),
        entries: folder?.entries ?? [],
        places: path === undefined ? PLACES : [],
      });
    // The backend refuses a file as a folder, and a path that is nothing.
    return Promise.reject(new FolderProblem("Cannot open that folder", path.endsWith(".mp4")));
  };
  const onChoose = vi.fn();
  render(
    <PathPicker
      mode={mode}
      list={source}
      // The server's answer: it knows a hidden file the listing leaves out.
      exists={(asked) =>
        Promise.resolve(asked === "/home/me/notes.txt" || asked === "/home/me/.hidden.csv")
      }
      title="Pick"
      onChoose={onChoose}
      onCancel={() => {}}
      {...extra}
    />,
  );
  return onChoose;
}

const list = () => screen.findByRole("listbox", { name: "Entries" });
const keys = async (...names: string[]) => {
  for (const key of names) fireEvent.keyDown(await list(), { key });
};
const path = () => (screen.getByRole("textbox", { name: "Path" }) as HTMLInputElement).value;

describe("path picker", () => {
  it("opens in the home folder, goes into a folder and back up, and chooses a folder", async () => {
    const choose = pick("folder");
    await screen.findByRole("option", { name: "clips" });
    expect(path()).toBe("/home/me");
    // Folders only; Enter opens the folder under the cursor.
    expect(screen.queryByRole("option", { name: "notes.txt" })).toBeNull();
    await keys("Enter");
    await vi.waitFor(() => expect(path()).toBe("/home/me/clips"));
    // The places stay, though a folder's own listing carries none.
    expect(screen.getByRole("button", { name: "Home" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Up" }));
    await vi.waitFor(() => expect(path()).toBe("/home/me"));
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(choose).toHaveBeenCalledExactlyOnceWith("/home/me");
  });

  it("chooses the file under the cursor, among the extensions asked for", async () => {
    const choose = pick("file", { extensions: [".mp4"] });
    await screen.findByRole("option", { name: "clips" });
    await keys("Enter");
    await screen.findByRole("option", { name: "b.mp4" });
    expect(screen.queryByRole("option", { name: "b.txt" })).toBeNull();
    await keys("ArrowDown", "Enter");
    expect(choose).toHaveBeenCalledExactlyOnceWith("/home/me/clips/b.mp4");
  });

  it("goes to a typed path, and a typed file path opens its folder with the file ready", async () => {
    const choose = pick("file");
    await screen.findByRole("option", { name: "clips" });
    const field = screen.getByRole("textbox", { name: "Path" });
    fireEvent.change(field, { target: { value: "/home/me/clips/b.mp4" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await screen.findByRole("option", { name: "b.mp4" });
    await vi.waitFor(() =>
      expect(screen.getByRole("option", { name: "b.mp4" }).ariaSelected).toBe("true"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(choose).toHaveBeenCalledExactlyOnceWith("/home/me/clips/b.mp4");
  });

  it("saves under a typed name, with the extension it offered, and asks before replacing a file", async () => {
    const choose = pick("save", { extensions: [".csv"], name: "marks" });
    await screen.findByRole("option", { name: "clips" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(choose).toHaveBeenCalledExactlyOnceWith("/home/me/marks.csv"));
    cleanup();

    const again = pick("save", { extensions: [".txt"], name: "notes" });
    await screen.findByRole("option", { name: "notes.txt" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Replace" }));
    expect(again).toHaveBeenCalledExactlyOnceWith("/home/me/notes.txt");
    cleanup();

    // A file the listing does not show asks too; a question that fails returns no path.
    const hidden = pick("save", { extensions: [".csv"], name: "marks" });
    await screen.findByRole("option", { name: "clips" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: ".hidden" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Replace" }));
    expect(hidden).toHaveBeenCalledExactlyOnceWith("/home/me/.hidden.csv");
    cleanup();
    const failed = pick("save", {
      name: "marks",
      exists: () => Promise.reject(new Error("Cannot check that file")),
    });
    await screen.findByRole("option", { name: "clips" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Cannot check that file");
    expect(failed).not.toHaveBeenCalled();
    cleanup();

    // A typed path to a new file opens its folder with the name filled; with no folder, Save stays off.
    const typed = pick("save", { extensions: [".csv"] });
    await screen.findByRole("option", { name: "clips" });
    const field = screen.getByRole("textbox", { name: "Path" });
    fireEvent.change(field, { target: { value: "/home/me/clips/new.csv" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await vi.waitFor(() => expect(path()).toBe("/home/me/clips"));
    expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(
      "new.csv",
    );
    fireEvent.change(field, { target: { value: "/nowhere/deep/new.csv" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await screen.findByText("Cannot open that folder");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(typed).not.toHaveBeenCalled();
  });
});
