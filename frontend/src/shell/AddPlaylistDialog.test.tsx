import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { HandlersInfo } from "../generated/api";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddPlaylistDialog } from "./AddPlaylistDialog";

const HANDLERS: HandlersInfo = {
  decoders: [
    {
      handler: "MOT_FILE",
      display_name: "MOT Annotations",
      extensions: [".txt"],
      live: true,
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
        { key: "videos_dir", label: "Videos", kind: { kind: "Folder" }, required: true },
        { key: "overlays_dir", label: "Overlays", kind: { kind: "Folder" }, required: true },
        { key: "handler_type", label: "Decoder", kind: { kind: "Handler" }, required: true },
        {
          key: "options",
          label: "Overlay options",
          kind: { kind: "DecoderOptions", handler_field: "handler_type" },
          required: false,
        },
        {
          key: "pairs",
          label: "Pairs",
          kind: {
            kind: "Scan",
            depends_on: ["videos_dir", "overlays_dir"],
            count: { missing: "Select folders.", found: "Found {count} pair(s).", none: "None." },
          },
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
  scan: () => ReturnType<typeof reply> | Promise<ReturnType<typeof reply>> = () =>
    reply(200, [
      { value: "SEQ-01", label: "SEQ-01", detail: "64×48" },
      { value: "SEQ-02", label: "SEQ-02", detail: "64×48" },
    ]),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/handlers") return reply(200, HANDLERS);
      if (url.endsWith("/resolve")) return resolve();
      return scan();
    }),
  );
}

const open = async (onClose = () => {}, resolverId?: string) => {
  render(
    <SessionProvider>
      <AddPlaylistDialog
        initial={resolverId ? { resolver: resolverId } : undefined}
        onClose={onClose}
      />
    </SessionProvider>,
  );
  await screen.findByLabelText("Playlist source");
  // The form's dialog opens in an effect after it mounts.
  await screen.findByRole("button", { name: "Cancel" });
};
const choose = (source: string) =>
  fireEvent.change(screen.getByLabelText("Playlist source"), { target: { value: source } });
const scanRoot = async (root = "/data") => {
  fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: root } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
  await screen.findByRole("checkbox", { name: /SEQ-01/ });
};
const scanMot = async () => {
  choose("mot_challenge");
  await scanRoot();
};
const settle = () => new Promise((done) => setTimeout(done, 10));
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
const add = () => screen.getByRole("button", { name: "Load Playlist" });
const clickAdd = () => fireEvent.click(add());
const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

describe("add playlist dialog", () => {
  it("locks the settings while finding playlists, ignores a second submit and adds under the latest name", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    await scanMot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Train " } });
    clickAdd();

    expect((await screen.findByRole("status")).textContent).toBe("Finding playlists…");
    expect(locked("Playlist source")).toBe(true);
    expect(locked("Dataset folder")).toBe(true);
    expect(locked("Name")).toBe(false);
    expect(screen.getByRole("button", { name: "Cancel" }).matches(":disabled")).toBe(false);
    clickAdd();
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
      reply(422, {
        code: "PlaylistInvalid",
        params: {},
        reason: "No MOT sequences found in '/data'.",
      }),
      reply(200, { item: "p1" }),
    ];
    stubBackend(() => answers.shift()!);
    await open(onClose);
    await scanMot();
    clickAdd();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No MOT sequences found in '/data'.");
    expect(screen.queryByRole("status")).toBeNull();
    expect(commands()).toHaveLength(0);
    expect(onClose).not.toHaveBeenCalled();
    expect(locked("Dataset folder")).toBe(false);
    expect(add()).toHaveProperty("disabled", false);

    clickAdd();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(commands()).toHaveLength(1);
  });

  it("aborts the request on Cancel and adds nothing when its success arrives late", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    await scanMot();
    clickAdd();
    await screen.findByRole("status");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const [call] = resolveCalls();
    expect(call?.[1]?.signal?.aborted).toBe(true);
    held.settle(reply(200, { item: "p1" }));
    await settle();

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(commands()).toEqual([]);
  });

  it("closes once on Cancel while the add is in flight, without aborting the resolution, and the add is acknowledged ok", async () => {
    const onClose = vi.fn();
    const held = deferred();
    stubBackend(() => held.promise);
    await open(onClose);
    await scanMot();
    clickAdd();
    await screen.findByRole("status");
    FakeSocket.latest.defer = true;
    held.settle(reply(200, { item: "p1" }));
    await waitFor(() => expect(commands()).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(resolveCalls()[0][1]?.signal?.aborted).toBe(false);
    const acks = vi.spyOn(FakeSocket.latest, "emit");
    act(() => FakeSocket.latest.flush());
    await settle();

    expect(acks).toHaveBeenCalledWith({
      type: "ack",
      seq: commands()[0]?.seq,
      outcome: { status: "ok" },
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps Load enabled with nothing checked, and says so instead of adding", async () => {
    const onClose = vi.fn();
    stubBackend();
    await open(onClose);
    await scanMot();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    expect(add()).toHaveProperty("disabled", false);
    clickAdd();
    expect((await screen.findByRole("alert")).textContent).toBe("No sequences selected.");
    expect(commands()).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  /** The dialog on a filled Folder Pair form whose pair count is held until `held` settles. */
  async function filledFolderPair() {
    const held = deferred();
    stubBackend(undefined, () => held.promise);
    await open();
    choose("folder_pair");
    expect(screen.queryByRole("textbox", { name: "Dataset folder" })).toBeNull();
    const decoder = screen.getByLabelText("Decoder") as HTMLSelectElement;
    expect(decoder.value).toBe("");
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/videos" } });
    fireEvent.change(screen.getByLabelText("Overlays"), { target: { value: "/overlays" } });
    expect(add()).toHaveProperty("disabled", true);
    fireEvent.change(decoder, { target: { value: "MOT_FILE" } });
    expect((decoder.selectedOptions[0] as HTMLOptionElement).text).toBe(
      "MOT Annotations (MOT_FILE)",
    );
    expect(add()).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "1920" } });
    expect(add()).toHaveProperty("disabled", false);
    return { held, decoder };
  }

  const FOLDER_PAIR_SETTINGS = {
    videos_dir: "/videos",
    overlays_dir: "/overlays",
    handler_type: "MOT_FILE",
    options: { width: 1920 },
  };

  it("loads a folder pair after counting its pairs, and not when another decoder is chosen meanwhile", async () => {
    const { held, decoder } = await filledFolderPair();

    // Load scans first; a decoder picked meanwhile keeps it from going on.
    clickAdd();
    await screen.findByText("Scanning…");
    expect(add()).toHaveProperty("disabled", true);
    fireEvent.change(decoder, { target: { value: "" } });
    held.settle(reply(200, [{ value: "a", label: "a", detail: "" }]));
    await screen.findByText("Found 1 pair(s).");
    expect(resolveCalls()).toHaveLength(0);

    // The count is kept, so loading with a decoder goes straight on.
    fireEvent.change(decoder, { target: { value: "MOT_FILE" } });
    clickAdd();
    await waitFor(() => expect(commands()).toHaveLength(1));
    const [url, init] = resolveCalls()[0];
    expect(url).toBe("/api/resolvers/folder_pair/resolve");
    expect(bodyOf(init)).toEqual({ session: "room-1", settings: FOLDER_PAIR_SETTINGS });
  });

  it("loads a folder pair whose decoder was changed away and back while its pairs were counted", async () => {
    const { held, decoder } = await filledFolderPair();
    clickAdd();
    await screen.findByText("Scanning…");
    fireEvent.change(decoder, { target: { value: "" } });
    fireEvent.change(decoder, { target: { value: "MOT_FILE" } });
    held.settle(reply(200, [{ value: "a", label: "a", detail: "" }]));
    await waitFor(() => expect(commands()).toHaveLength(1));
    expect(bodyOf(resolveCalls()[0][1])).toEqual({
      session: "room-1",
      settings: FOLDER_PAIR_SETTINGS,
    });
  });

  it("keeps Cancel aborting a load of another source when an earlier load's count answers late", async () => {
    const counting = deferred();
    const resolving = deferred();
    let scans = 0;
    stubBackend(
      () => resolving.promise,
      () =>
        scans++ === 0
          ? counting.promise
          : reply(200, [{ value: "SEQ-01", label: "SEQ-01", detail: "" }]),
    );
    await open();
    choose("folder_pair");
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/videos" } });
    fireEvent.change(screen.getByLabelText("Overlays"), { target: { value: "/overlays" } });
    fireEvent.change(screen.getByLabelText("Decoder"), { target: { value: "MOT_FILE" } });
    fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "1920" } });
    clickAdd();
    await screen.findByText("Scanning…");

    await scanMot();
    clickAdd();
    await waitFor(() => expect(resolveCalls()).toHaveLength(1));
    counting.settle(reply(200, [{ value: "a", label: "a", detail: "" }]));
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(resolveCalls()[0][1]?.signal?.aborted).toBe(true);
    resolving.settle(reply(200, { item: "p1" }));
    await settle();
    expect(commands()).toEqual([]);
  });

  it("keeps each source's form while the user switches between sources", async () => {
    stubBackend();
    await open();
    choose("folder_pair");
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/videos" } });
    fireEvent.change(screen.getByLabelText("Decoder"), { target: { value: "MOT_FILE" } });
    choose("mot_challenge");
    await scanRoot("/data");
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));

    choose("folder_pair");
    expect((screen.getByLabelText("Videos") as HTMLInputElement).value).toBe("/videos");
    expect((screen.getByLabelText("Decoder") as HTMLSelectElement).value).toBe("MOT_FILE");
    choose("mot_challenge");
    expect((screen.getByLabelText("Dataset folder") as HTMLInputElement).value).toBe("/data");
    expect((screen.getByRole("checkbox", { name: /SEQ-01/ }) as HTMLInputElement).checked).toBe(
      true,
    );
    expect((screen.getByRole("checkbox", { name: /SEQ-02/ }) as HTMLInputElement).checked).toBe(
      false,
    );
    // Only the source on show is added.
    clickAdd();
    await waitFor(() => expect(commands()).toHaveLength(1));
    const requests = resolveCalls();
    expect(requests).toHaveLength(1);
    expect(String(requests[0][0])).toContain("mot_challenge");
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

  it("explains the choice and lists the sources until one is chosen", async () => {
    stubBackend();
    await open();
    expect(screen.getByText(/^Build a playlist from video and overlay files/)).toBeTruthy();
    expect(screen.getByText("MOT Challenge: Reads MOT sequences.")).toBeTruthy();
    choose("folder_pair");
    // The page stays, drawn out of sight, so choosing a source does not resize the dialog.
    expect(screen.getByText(/^Build a playlist/).closest("[inert]")).not.toBeNull();
  });

  it("opens on the source it is given, and on the list when there is no such source", async () => {
    stubBackend();
    await open(() => {}, "folder_pair");
    expect(screen.getByLabelText<HTMLSelectElement>("Playlist source").value).toBe("folder_pair");
    expect(screen.getByLabelText("Videos")).toBeTruthy();
    cleanup();
    await open(() => {}, "gone");
    expect(screen.getByLabelText<HTMLSelectElement>("Playlist source").value).toBe("");
    expect(screen.getByText(/^Build a playlist/)).toBeTruthy();
  });
});

describe("edit playlist dialog", () => {
  const playlist = scenario("playlist");
  const edit = async (resolver: string, settings = {}) => {
    stubBackend();
    FakeSocket.launch = { scenario: playlist, state: "initial" };
    render(
      <SessionProvider>
        <AddPlaylistDialog
          editing={playlist.ids.list}
          initial={{ resolver, label: "Train", settings }}
          onClose={() => {}}
        />
      </SessionProvider>,
    );
    await screen.findByRole("dialog", { name: "Edit Playlist" });
  };

  it("starts from the saved source, settings and name, keeps the source, and saves the resolved playlists over the item", async () => {
    await edit("mot_challenge", { root: "/data", sequences: ["SEQ-01"] });
    expect(((await screen.findByLabelText("Dataset folder")) as HTMLInputElement).value).toBe(
      "/data",
    );
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Train");
    expect(locked("Playlist source")).toBe(true);

    await screen.findByRole("checkbox", { name: /SEQ-01/ });
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Later" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(commands()).toHaveLength(1));
    expect(commands()[0]).toMatchObject({
      cmd: {
        type: "content",
        command: { type: "editResolved", item: playlist.ids.list, ticket: "p1", label: "Later" },
      },
    });
    expect(bodyOf(resolveCalls()[0][1])).toEqual({
      session: "room-1",
      settings: { root: "/data", sequences: ["SEQ-01", "SEQ-02"] },
    });
  });

  it("saves a new name alone without finding the playlists again", async () => {
    await edit("mot_challenge", { root: "/data", sequences: ["SEQ-01"] });
    await screen.findByRole("checkbox", { name: /SEQ-01/ });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Later " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(commands()).toHaveLength(1));
    expect(commands()[0]).toMatchObject({
      cmd: { command: { type: "renameItem", item: playlist.ids.list, label: "Later" } },
    });
    expect(resolveCalls()).toEqual([]);
  });

  it("says a source this installation lacks is unavailable and saves nothing", async () => {
    await edit("gone", { root: "/data" });
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Playlist source 'gone' is unavailable.",
    );
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(locked("Playlist source")).toBe(true);
    expect(screen.getByRole("button", { name: "Cancel" }).matches(":disabled")).toBe(false);
  });
});
