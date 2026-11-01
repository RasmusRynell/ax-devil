import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { HandlersInfo } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddPlaylistDialog } from "./AddPlaylistDialog";

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

/** The backend: the handlers, and a scan that finds two sequences. */
function stubBackend() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/handlers") return reply(200, HANDLERS);
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
  await screen.findByLabelText("Dataset folder");
  // The form's dialog opens in an effect after it mounts.
  await screen.findByRole("button", { name: "Add" });
};
const scanRoot = async (root = "/data") => {
  fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: root } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
  await screen.findByRole("checkbox", { name: /SEQ-01/ });
};
const add = () => screen.getByRole("button", { name: "Add" });
const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

describe("add playlist dialog", () => {
  it("adds the scanned playlist under its name, to resolve in the background, and closes", async () => {
    const onClose = vi.fn();
    stubBackend();
    await open(onClose);
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
            type: "addItems",
            items: [
              {
                type: "Playlist",
                resolver: "mot_challenge",
                settings: { root: "/data", sequences: ["SEQ-01"] },
                label: "Train",
              },
            ],
            open: true,
          },
        },
      },
    ]);
  });

  it("stays open with the refusal under the form, until the settings change", async () => {
    const onClose = vi.fn();
    stubBackend();
    await open(onClose);
    await scanRoot();
    FakeSocket.refuseContent = "Invalid";
    fireEvent.click(add());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("cannot open");
    expect(onClose).not.toHaveBeenCalled();
    expect(add()).toHaveProperty("disabled", false);

    fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: "/other" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("builds the form of the chosen resolver, with the decoders to choose from", async () => {
    stubBackend();
    await open();
    fireEvent.change(screen.getByLabelText("Playlist type"), { target: { value: "folder_pair" } });
    expect(screen.queryByLabelText("Dataset folder")).toBeNull();
    const format = screen.getByLabelText("Overlay format") as HTMLSelectElement;
    expect(format.value).toBe("MOT_FILE");
    fireEvent.change(screen.getByLabelText("Videos folder"), { target: { value: "/videos" } });
    expect(add()).toHaveProperty("disabled", false);
  });

  it("says why the handlers could not be loaded, in the backend's words when it sends them", async () => {
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
  });
});
