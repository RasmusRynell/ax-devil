import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { HandlersInfo } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddPlaylistDialog } from "./AddPlaylistDialog";

const HANDLERS: HandlersInfo = {
  decoders: [
    {
      handler: "MOT_FILE",
      display_name: "MOT Annotations",
      extensions: [".txt"],
      options: [
        { key: "width", label: "Image width", kind: { kind: "Integer", min: 1n }, required: true },
      ],
    },
  ],
  resolvers: [
    {
      id: "mot_challenge",
      display_name: "MOT Challenge",
      description: "Reads MOT sequences.",
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
        {
          key: "options",
          label: "Overlay options",
          kind: { kind: "DecoderOptions", handler_field: "handler_type" },
          required: false,
        },
      ],
    },
  ],
};

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

const reply = (status: number, json: unknown) => ({
  ok: status < 400,
  status,
  json: async () => json,
});

/** The backend: the handlers, a resolve that answers an item, and a scan that finds two sequences. */
function stubBackend(resolve: () => ReturnType<typeof reply> = () => reply(200, { item: "p1" })) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/handlers") return reply(200, HANDLERS);
      if (url.endsWith("/resolve")) return resolve();
      return reply(200, [
        { value: "SEQ-01", label: "SEQ-01", detail: "64×48" },
        { value: "SEQ-02", label: "SEQ-02", detail: "64×48" },
      ]);
    }),
  );
}

const open = async (onClose = () => {}) => {
  render(
    <SessionProvider>
      <AddPlaylistDialog onClose={onClose} />
    </SessionProvider>,
  );
  await screen.findByLabelText("Playlist source");
  // The form's dialog opens in an effect after it mounts.
  await screen.findByRole("button", { name: "Add" });
};
const choose = (source: string) =>
  fireEvent.change(screen.getByLabelText("Playlist source"), { target: { value: source } });
const scanRoot = async (root = "/data") => {
  fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: root } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
  await screen.findByRole("checkbox", { name: /SEQ-01/ });
};
/** The JSON a request carried. */
const bodyOf = (init?: RequestInit): unknown => JSON.parse(init?.body as string);
const add = () => screen.getByRole("button", { name: "Add" });
const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

describe("add playlist dialog", () => {
  it("resolves the scanned playlist, adds the result under its name and closes", async () => {
    const onClose = vi.fn();
    stubBackend();
    await open(onClose);
    // No source is chosen for the user: the sources are listed, and nothing can be added.
    expect((screen.getByLabelText("Playlist source") as HTMLSelectElement).value).toBe("");
    expect(screen.getByText("MOT Challenge: Reads MOT sequences.")).toBeTruthy();
    expect(add()).toHaveProperty("disabled", true);
    choose("mot_challenge");
    expect(add()).toHaveProperty("disabled", true);
    await scanRoot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Train " } });
    expect(add()).toHaveProperty("disabled", false);
    fireEvent.click(add());
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    expect(commands()).toMatchObject([
      {
        cmd: {
          type: "content",
          command: {
            type: "addResolved",
            item: "p1",
            label: "Train",
          },
        },
      },
    ]);
    expect(bodyOf(vi.mocked(fetch).mock.calls.at(-1)?.[1])).toEqual({
      session: "room-1",
      settings: { root: "/data", sequences: ["SEQ-01"] },
    });
  });

  it("stays open with the refusal under the form, until the settings change", async () => {
    const onClose = vi.fn();
    stubBackend(() =>
      reply(422, { code: "PlaylistInvalid", params: {}, reason: "No MOT sequences found" }),
    );
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(add());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No MOT sequences found");
    expect(commands()).toHaveLength(0);
    expect(onClose).not.toHaveBeenCalled();
    expect(add()).toHaveProperty("disabled", false);

    fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: "/other" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps Load enabled with nothing checked, and says so instead of adding", async () => {
    const onClose = vi.fn();
    stubBackend();
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    expect(add()).toHaveProperty("disabled", false);
    fireEvent.click(add());
    expect((await screen.findByRole("alert")).textContent).toBe("No sequences selected.");
    expect(commands()).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("builds the form of the chosen resolver, with the chosen decoder's options sent as a map", async () => {
    stubBackend();
    await open();
    choose("folder_pair");
    expect(screen.queryByLabelText("Dataset folder")).toBeNull();
    const format = screen.getByLabelText("Overlay format") as HTMLSelectElement;
    expect(format.value).toBe("");
    fireEvent.change(screen.getByLabelText("Videos folder"), { target: { value: "/videos" } });
    expect(add()).toHaveProperty("disabled", true);
    fireEvent.change(format, { target: { value: "MOT_FILE" } });
    expect(add()).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "1920" } });
    expect(add()).toHaveProperty("disabled", false);
    fireEvent.click(add());
    await waitFor(() => expect(commands()).toHaveLength(1));
    const [url, init] =
      vi.mocked(fetch).mock.calls.find(([url]) => url === "/api/resolvers/folder_pair/resolve") ??
      [];
    expect(url).toBe("/api/resolvers/folder_pair/resolve");
    expect(bodyOf(init)).toEqual({
      session: "room-1",
      settings: { videos_dir: "/videos", handler_type: "MOT_FILE", options: { width: 1920 } },
    });
  });

  it("says why the handlers could not be loaded, in the backend's words when it sends them, and retries", async () => {
    const failure = (error: unknown) => vi.stubGlobal("fetch", async () => reply(403, error));
    failure({ code: "Internal", params: {}, reason: "Handlers unavailable" });
    render(
      <SessionProvider>
        <AddPlaylistDialog onClose={() => {}} />
      </SessionProvider>,
    );
    expect((await screen.findByRole("alert")).textContent).toContain("Handlers unavailable");
    cleanup();
    failure({ code: "Internal", params: {} });
    render(
      <SessionProvider>
        <AddPlaylistDialog onClose={() => {}} />
      </SessionProvider>,
    );
    expect((await screen.findByRole("alert")).textContent).toContain("The backend failed.");
    // Retry asks again.
    stubBackend();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Playlist source")).toBeTruthy();
  });
});
