import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DecoderInfo } from "../generated/api";
import { GrantsContext } from "../access";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddVideoDialog } from "./AddVideoDialog";
import { decodersFor } from "./OverlayRow";
import type { WindowBridge } from "./windowBridge";

const decoder = (handler: string, ...extensions: string[]): DecoderInfo => ({
  handler,
  display_name: handler,
  extensions,
  live: true,
  options: [],
});

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.axDevilWindow;
  window.sessionStorage.clear();
  FakeSocket.reset();
});

it("picks the decoders that may read the file: no extensions, or an extension ending its name", () => {
  const decoders = [
    decoder("JSONL", ".jsonl"),
    decoder("ACME", ".acme.jsonl"),
    decoder("MOT", ".txt"),
    decoder("ANY"),
  ];
  const handlers = (path: string) => decodersFor(decoders, path).map((one) => one.handler);

  expect(handlers("/data/run.ACME.jsonl")).toEqual(["JSONL", "ACME", "ANY"]);
  expect(handlers("C:\\data\\gt.txt")).toEqual(["MOT", "ANY"]);
  expect(handlers("/data/notes.mp3")).toEqual(["ANY"]);
  expect(handlers("/data/.txt")).toEqual(["ANY"]);
  expect(decodersFor(decoders.slice(0, 3), "/data/notes.mp3")).toEqual([]);
});

const reply = (status: number, json: unknown) => ({
  ok: status < 400,
  status,
  json: async () => json,
});
/** The backend: the handlers, and every file readable except those named in `missing`. */
const serve = (decoders: unknown[], missing: string[] = []) =>
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) =>
    url === "/api/files/check"
      ? reply(
          200,
          (JSON.parse(init?.body as string) as { paths: string[] }).paths.map((path) => ({
            path,
            ...(missing.includes(path) && { problem: "File not found" }),
          })),
        )
      : reply(200, { decoders, resolvers: [] }),
  );
const open = async () => {
  render(
    <SessionProvider>
      <AddVideoDialog onClose={() => {}} />
    </SessionProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Video path"), { target: { value: "/a.mp4" } });
};
const overlays = () => screen.findAllByLabelText("Overlay path");
const overlay = async (path: string, at = 0) =>
  fireEvent.change((await overlays())[at], { target: { value: path } });
const handlerAt = (at = 0) => screen.getAllByLabelText("Data handler")[at] as HTMLSelectElement;
const add = () => screen.getByRole("button", { name: "Add" });
/** Waits for the backend to answer about the files typed, which Add waits for too. */
const answered = () => waitFor(() => expect(add()).toHaveProperty("disabled", false));
const sentOverlays = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" &&
    message.cmd.type === "content" &&
    message.cmd.command.type === "addItems" &&
    message.cmd.command.items[0].type === "Video"
      ? [message.cmd.command.items[0].overlays.map(({ path, handler }) => [path, handler])]
      : [],
  );

it("offers every handler, chooses the sole one that reads the file, and waits for a choice otherwise", async () => {
  const decoders = [
    decoder("V1", ".jsonl"),
    decoder("V2", ".jsonl"),
    decoder("X1", ".xml"),
    decoder("MOT", ".txt"),
  ];
  serve(decoders);
  await open();
  expect(handlerAt().disabled).toBe(true);

  await overlay("/data/gt.txt");
  expect(handlerAt().value).toBe("MOT");
  expect(handlerAt().options).toHaveLength(5);
  await answered();

  // Any handler can be chosen by hand, then replaced by the one that reads the next file.
  fireEvent.change(handlerAt(), { target: { value: "X1" } });
  expect(handlerAt().value).toBe("X1");
  await overlay("/data/gt2.txt");
  expect(handlerAt().value).toBe("MOT");

  await overlay("/data/v1.jsonl");
  expect(handlerAt().value).toBe("");
  expect(screen.getByRole("alert").textContent).toBe(
    "Choose the data handler that reads v1.jsonl.",
  );
  expect(add()).toHaveProperty("disabled", true);

  fireEvent.change(handlerAt(), { target: { value: "V2" } });
  expect(screen.queryByRole("alert")).toBeNull();
  await answered();

  // A pick that does not read the new file is dropped, and does not come back for a later one.
  await overlay("/data/c.mp3");
  expect(handlerAt().value).toBe("");
  expect(add()).toHaveProperty("disabled", true);
  await overlay("/data/other.jsonl");
  expect(handlerAt().value).toBe("");
});

it("adds overlays one by one, ignores a blank one and needs a video", async () => {
  serve([decoder("V1", ".jsonl"), decoder("MOT", ".txt")]);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Add overlay" }));
  fireEvent.click(screen.getByRole("button", { name: "Add overlay" }));
  expect(await overlays()).toHaveLength(3);
  expect(document.activeElement).toBe((await overlays())[2]);

  await overlay("/data/a.jsonl", 0);
  await overlay("/data/gt.txt", 2);
  fireEvent.click(screen.getByRole("button", { name: "Remove overlay 1" }));
  expect(await overlays()).toHaveLength(2);
  expect(screen.getAllByRole("group").map((row) => row.getAttribute("aria-label"))).toEqual([
    "Overlay 1",
    "Overlay 2",
  ]);
  expect(handlerAt(1).value).toBe("MOT");

  fireEvent.change(screen.getByLabelText("Video path"), { target: { value: "" } });
  expect(add()).toHaveProperty("disabled", true);
  expect(screen.getByRole("alert").textContent).toBe("Choose a video file to continue.");
  fireEvent.change(screen.getByLabelText("Video path"), { target: { value: "/a.mp4" } });
  await answered();
  fireEvent.click(add());
  await waitFor(() => expect(sentOverlays()).toEqual([[["/data/gt.txt", "MOT"]]]));
});

it("keeps a required option of the handler from being skipped: Add says it is required until it is filled", async () => {
  const mot: DecoderInfo = {
    ...decoder("MOT", ".txt"),
    options: [
      { key: "width", label: "Image width", kind: { kind: "Integer", min: 1n }, required: true },
    ],
  };
  serve([mot]);
  await open();
  await overlay("/data/gt.txt");
  await answered();

  fireEvent.click(add());
  expect((await screen.findByRole("alert")).textContent).toBe("Image width is required");
  expect(sentOverlays()).toEqual([]);

  fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "1920" } });
  fireEvent.click(add());
  await waitFor(() => expect(sentOverlays()).toEqual([[["/data/gt.txt", "MOT"]]]));
});

it("says why the handlers could not be loaded and asks again on Retry", async () => {
  vi.stubGlobal("fetch", async () =>
    reply(403, { code: "Internal", params: {}, reason: "Handlers unavailable" }),
  );
  await open();
  expect((await screen.findByRole("alert")).textContent).toContain("Handlers unavailable");
  serve([]);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByLabelText("Overlay path")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("keeps Add off, with the reason, while the backend cannot read the video or an overlay", async () => {
  serve([decoder("MOT", ".txt")], ["/gone.mp4", "/gone.txt"]);
  await open();
  await overlay("/gone.txt");
  // Add also waits for the answer.
  expect(add()).toHaveProperty("disabled", true);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("File not found"));
  expect(add()).toHaveProperty("disabled", true);

  await overlay("/gt.txt");
  await answered();
  expect(screen.queryByRole("alert")).toBeNull();

  fireEvent.change(screen.getByLabelText("Video path"), { target: { value: "/gone.mp4" } });
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("File not found"));
  expect(add()).toHaveProperty("disabled", true);
});

it("asks again when a path comes back after an edit: the files may have changed, so Add waits for the new answer", async () => {
  const missing: string[] = [];
  serve([], missing);
  await open();
  await answered();

  missing.push("/a.mp4");
  const video = screen.getByLabelText("Video path");
  fireEvent.change(video, { target: { value: "/b.mp4" } });
  fireEvent.change(video, { target: { value: "/a.mp4" } });
  expect(add()).toHaveProperty("disabled", true);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("File not found"));
  expect(add()).toHaveProperty("disabled", true);
});

it("fills the path from the file chooser, keeps it when the chooser is cancelled, and names the video as typed or by its file", async () => {
  serve([]);
  const picks = [["/clips/b.mp4"], []];
  window.axDevilWindow = {
    pickVideos: () => Promise.resolve(picks.shift() ?? []),
  } as unknown as WindowBridge;
  render(
    <GrantsContext.Provider value={["preferences"]}>
      <SessionProvider>
        <AddVideoDialog onClose={() => {}} />
      </SessionProvider>
    </GrantsContext.Provider>,
  );
  const video = (await screen.findByLabelText("Video path")) as HTMLInputElement;
  const name = screen.getByLabelText("Name") as HTMLInputElement;
  expect(name.placeholder).toBe("Optional");

  fireEvent.click(screen.getByRole("button", { name: "Choose video file" }));
  await waitFor(() => expect(video.value).toBe("/clips/b.mp4"));
  expect(name.placeholder).toBe("b.mp4");
  fireEvent.click(screen.getByRole("button", { name: "Choose video file" }));
  await act(async () => {});
  expect(video.value).toBe("/clips/b.mp4");

  await answered();
  fireEvent.change(name, { target: { value: " Take 2 " } });
  fireEvent.click(add());
  await waitFor(() =>
    expect(FakeSocket.latest.received).toContainEqual(
      expect.objectContaining({
        cmd: expect.objectContaining({
          command: expect.objectContaining({
            items: [expect.objectContaining({ path: "/clips/b.mp4", label: "Take 2" })],
          }),
        }),
      }),
    ),
  );
});

it.each([
  [
    "fails",
    () => Promise.reject(new Error("The file dialog could not open")),
    "The file dialog could not open",
  ],
  [
    "throws",
    () => {
      throw new Error("The file dialog is unavailable");
    },
    "The file dialog is unavailable",
  ],
  [
    "returns several videos",
    () => Promise.resolve(["/clips/a.mp4", "/clips/b.mp4"]),
    "Select one video.",
  ],
])(
  "says why when the file chooser %s, and leaves the path and the session alone",
  async (_, pick, reason) => {
    serve([]);
    window.axDevilWindow = { pickVideos: pick } as unknown as WindowBridge;
    render(
      <GrantsContext.Provider value={["preferences"]}>
        <SessionProvider>
          <AddVideoDialog onClose={() => {}} />
        </SessionProvider>
      </GrantsContext.Provider>,
    );
    const video = (await screen.findByLabelText("Video path")) as HTMLInputElement;
    fireEvent.change(video, { target: { value: "/clips/a.mp4" } });

    fireEvent.click(screen.getByRole("button", { name: "Choose video file" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(reason));
    expect(video.value).toBe("/clips/a.mp4");
    expect(FakeSocket.latest.received.filter((message) => message.type === "command")).toEqual([]);
  },
);
