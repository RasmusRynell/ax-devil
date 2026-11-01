import { afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { HandlersInfo } from "../generated/api";
import { fakeWorkspace } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { AddPlaylistDialog } from "./AddPlaylistDialog";
import { Shell } from "./Shell";

const HANDLERS: HandlersInfo = {
  decoders: [{ handler: "MOT_FILE", display_name: "MOT Annotations", extensions: [".txt"] }],
  resolvers: [
    {
      id: "mot_challenge",
      display_name: "MOT Challenge",
      description: "",
      fields: [
        { key: "root", label: "Dataset folder", kind: { kind: "Folder" }, required: true },
        {
          key: "sequences",
          label: "Sequences",
          kind: { kind: "Scan", depends_on: ["root"] },
          required: false,
        },
      ],
    },
    {
      id: "folder_pair",
      display_name: "Folder pair",
      description: "",
      fields: [
        { key: "videos_dir", label: "Videos folder", kind: { kind: "Folder" }, required: true },
        { key: "handler_type", label: "Overlay format", kind: { kind: "Handler" }, required: true },
      ],
    },
  ],
};

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const reply = (status: number, json: unknown) => ({
  ok: status < 400,
  status,
  json: async () => json,
});

/** The backend: handlers, a scan finding two sequences, and `playlist` deciding the answer to a load. */
function stubBackend(playlist: (init: RequestInit) => Promise<unknown>) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    if (url === "/api/handlers") return reply(200, HANDLERS);
    if (url.endsWith("/scan")) {
      return reply(200, [
        { value: "SEQ-01", label: "SEQ-01", detail: "64×48" },
        { value: "SEQ-02", label: "SEQ-02", detail: "64×48" },
      ]);
    }
    return playlist(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const open = async (onClose = () => {}) => {
  render(<AddPlaylistDialog onClose={onClose} />);
  await screen.findByLabelText("Dataset folder");
};
const scanRoot = async (root = "/data") => {
  fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: root } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
  await screen.findByRole("checkbox", { name: /SEQ-01/ });
};
const load = () => screen.getByRole("button", { name: "Load" });

describe("add playlist dialog", () => {
  it("loads the scanned playlist under its name and closes", async () => {
    const onClose = vi.fn();
    const fetchMock = stubBackend(async () => reply(200, {}));
    await open(onClose);
    expect(load()).toHaveProperty("disabled", true);
    await scanRoot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Train " } });
    expect(load()).toHaveProperty("disabled", false);
    fireEvent.click(load());
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit];
    expect(url).toBe("/api/workspace/playlists");
    expect(JSON.parse(init.body as string)).toEqual({
      resolver: "mot_challenge",
      settings: { root: "/data", sequences: ["SEQ-01"] },
      label: "Train",
    });
  });

  it("stays open with a refusal under the field it names, until that is changed", async () => {
    const onClose = vi.fn();
    stubBackend(async () => reply(422, { reason: "No sequences in it", location: "root" }));
    await open(onClose);
    await scanRoot();
    fireEvent.click(load());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No sequences in it");
    expect(alert.parentElement?.contains(screen.getByLabelText("Dataset folder"))).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
    expect(load()).toHaveProperty("disabled", false);

    fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: "/other" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a refusal that names no field under the form", async () => {
    stubBackend(async () => reply(422, { reason: "Nothing to play", location: "mot_challenge" }));
    await open();
    await scanRoot();
    fireEvent.click(load());
    expect((await screen.findByRole("alert")).textContent).toContain("Nothing to play");
  });

  it("builds the form of the chosen resolver, with the decoders to choose from", async () => {
    stubBackend(async () => reply(200, {}));
    await open();
    fireEvent.change(screen.getByLabelText("Playlist type"), { target: { value: "folder_pair" } });
    expect(screen.queryByLabelText("Dataset folder")).toBeNull();
    const format = screen.getByLabelText("Overlay format") as HTMLSelectElement;
    expect(format.value).toBe("MOT_FILE");
    fireEvent.change(screen.getByLabelText("Videos folder"), { target: { value: "/videos" } });
    expect(load()).toHaveProperty("disabled", false);
  });

  it("tells the backend to stop resolving when it is closed", async () => {
    let signal: AbortSignal | undefined;
    stubBackend(
      (init) =>
        new Promise(() => {
          signal = init.signal as AbortSignal;
        }),
    );
    const onClose = vi.fn();
    render(<AddPlaylistDialog onClose={onClose} />);
    await screen.findByLabelText("Dataset folder");
    await scanRoot();
    fireEvent.click(load());
    await waitFor(() => expect(signal).toBeDefined());
    expect(load()).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    cleanup();
    expect(signal?.aborted).toBe(true);
  });

  it("says why the handlers could not be loaded", async () => {
    vi.stubGlobal("fetch", async () =>
      reply(500, { reason: "Handlers unavailable", location: "" }),
    );
    render(<AddPlaylistDialog onClose={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Handlers unavailable");
  });
});

describe("add playlist command", () => {
  it("opens from the shortcut and from the File menu", async () => {
    stubBackend(async () => reply(200, {}));
    const workspace = fakeWorkspace();
    render(
      <FakeSettings state={DEFAULT_SETTINGS}>
        <WorkspaceProvider store={workspace.store}>
          <Shell />
        </WorkspaceProvider>
      </FakeSettings>,
    );
    await act(async () => fireEvent.keyDown(document, { key: "N", ctrlKey: true, shiftKey: true }));
    expect(await screen.findByRole("dialog", { name: "Add Playlist" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Playlist/ }));
    expect(await screen.findByRole("dialog", { name: "Add Playlist" })).toBeTruthy();
  });
});
