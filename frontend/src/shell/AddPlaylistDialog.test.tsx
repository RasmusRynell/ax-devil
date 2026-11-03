import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { HandlersInfo } from "../generated/api";
import { FakeSocket, type Room } from "../session/fakeSession";
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
function stubBackend(
  resolve: () => ReturnType<typeof reply> | Promise<ReturnType<typeof reply>> = () =>
    reply(200, { item: "p1" }),
) {
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
/** The requests to the resolve route so far. */
const resolveCalls = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([url]) => typeof url === "string" && url.endsWith("/resolve"));
/** The JSON a request carried. */
const bodyOf = (init?: RequestInit): unknown => JSON.parse(init?.body as string);
/** Whether the field is disabled, by itself or by its fieldset. */
const locked = (label: string) => screen.getByLabelText(label).matches(":disabled");
/** A response the test settles when it chooses, to hold a request in flight. */
function deferred() {
  let settle: (response: ReturnType<typeof reply>) => void = () => {};
  const promise = new Promise<ReturnType<typeof reply>>((done) => (settle = done));
  return { promise, settle };
}
const add = () => screen.getByRole("button", { name: "Add" });
const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

describe("add playlist dialog", () => {
  it("locks the settings while finding playlists, ignores a second submit and adds under the latest name", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Train " } });
    fireEvent.click(add());

    expect((await screen.findByRole("status")).textContent).toBe("Finding playlists…");
    expect(locked("Playlist source")).toBe(true);
    expect(locked("Dataset folder")).toBe(true);
    expect(locked("Name")).toBe(false);
    expect(screen.getByRole("button", { name: "Cancel" }).matches(":disabled")).toBe(false);
    fireEvent.submit(screen.getByLabelText("Name").closest("form")!);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Later " } });
    held.settle(reply(200, { item: "p1" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    expect(commands()).toMatchObject([
      { cmd: { type: "content", command: { type: "addResolved", item: "p1", label: "Later" } } },
    ]);
    const requests = resolveCalls();
    expect(requests).toHaveLength(1);
    expect(bodyOf(requests[0][1])).toEqual({
      session: "room-1",
      settings: { root: "/data", sequences: ["SEQ-01"] },
    });
  });

  it("shows a refusal in the dialog, unlocks the settings, adds nothing and lets the user retry", async () => {
    const onClose = vi.fn();
    let answers = [
      reply(422, { code: "PlaylistInvalid", params: {}, reason: "No MOT sequences found" }),
      reply(200, { item: "p1" }),
    ];
    stubBackend(() => answers.shift()!);
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(add());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No MOT sequences found");
    expect(screen.queryByRole("status")).toBeNull();
    expect(commands()).toHaveLength(0);
    expect(onClose).not.toHaveBeenCalled();
    expect(locked("Dataset folder")).toBe(false);
    expect(add()).toHaveProperty("disabled", false);

    fireEvent.click(add());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(commands()).toHaveLength(1);
  });

  it("aborts the request on Cancel and adds nothing when its success arrives late", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(add());
    await screen.findByRole("status");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const [call] = resolveCalls();
    expect(call?.[1]?.signal?.aborted).toBe(true);
    held.settle(reply(200, { item: "p1" }));
    await new Promise((done) => setTimeout(done, 10));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(commands()).toEqual([]);
  });

  it("closes once on Cancel while the add is in flight, and the add completes", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    choose("mot_challenge");
    await scanRoot();
    fireEvent.click(add());
    await screen.findByRole("status");
    FakeSocket.latest.defer = true;
    held.settle(reply(200, { item: "p1" }));
    await waitFor(() => expect(commands()).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(resolveCalls()[0][1]?.signal?.aborted).toBe(false);
    act(() => FakeSocket.latest.flush());
    await new Promise((done) => setTimeout(done, 10));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect((FakeSocket.rooms.get("room-1") as Room).items).toHaveLength(1);
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

  it("keeps each source's form while the user switches between sources", async () => {
    stubBackend();
    await open();
    choose("folder_pair");
    fireEvent.change(screen.getByLabelText("Videos folder"), { target: { value: "/videos" } });
    fireEvent.change(screen.getByLabelText("Overlay format"), { target: { value: "MOT_FILE" } });
    choose("mot_challenge");
    await scanRoot("/data");
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));

    choose("folder_pair");
    expect((screen.getByLabelText("Videos folder") as HTMLInputElement).value).toBe("/videos");
    expect((screen.getByLabelText("Overlay format") as HTMLSelectElement).value).toBe("MOT_FILE");
    choose("mot_challenge");
    expect((screen.getByLabelText("Dataset folder") as HTMLInputElement).value).toBe("/data");
    expect((screen.getByRole("checkbox", { name: /SEQ-01/ }) as HTMLInputElement).checked).toBe(
      true,
    );
    expect((screen.getByRole("checkbox", { name: /SEQ-02/ }) as HTMLInputElement).checked).toBe(
      false,
    );
    // Only the source on show is added.
    fireEvent.click(add());
    await waitFor(() => expect(commands()).toHaveLength(1));
    expect(commands()).toMatchObject([
      { cmd: { command: { items: [{ resolver: "mot_challenge" }] } } },
    ]);
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
