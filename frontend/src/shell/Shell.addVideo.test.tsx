import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { FakeSocket } from "../session/fakeSession";
import { renderShell } from "./shellHarness";
import type { WindowBridge } from "../windowBridge";

// The added video's viewer is not under test, and needs the playback worker.
vi.mock("../playback/VideoViewer", () => ({ VideoViewer: () => null }));

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  delete window.axDevilWindow;
});

/** Waits for Add, which waits for the backend's word on the files typed. */
const enabled = () =>
  waitFor(() =>
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(false),
  );

describe("add video", () => {
  const chooses = (video: string | null) => {
    window.axDevilWindow = {
      minimize() {},
      toggleMaximize() {},
      close() {},
      toggleFullScreen() {},
      pickVideo: () => Promise.resolve(video),
      pickOverlay: () => Promise.resolve(null),
      connect: () => Promise.resolve(),
      disconnect: () => Promise.resolve(),
      restart() {},
      pickFolder: () => Promise.resolve(null),
      pickExportFile: () => Promise.resolve(null),
      pickOpenWorkspace: () => Promise.resolve(null),
      pickSaveWorkspace: () => Promise.resolve(null),
      appVersion: async () => "0.0.0",
      openLogs: () => Promise.resolve(),
      onMaximizedChange: () => () => {},
      onFullScreenChange: () => () => {},
    } satisfies WindowBridge;
  };
  const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

  const serve = () =>
    vi.stubGlobal(
      "fetch",
      async (url: string) =>
        new Response(
          JSON.stringify(url === "/api/files/check" ? [] : { decoders: [], resolvers: [] }),
        ),
    );
  it("opens the dialog, sends nothing until Add, and adds the file the chooser gave", async () => {
    serve();
    chooses("/clips/a.mp4");
    await renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Video/ }));
    await screen.findByRole("dialog", { name: "Add Video" });
    fireEvent.click(screen.getByRole("button", { name: "Choose video file" }));
    await enabled();
    expect(commands()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await act(async () => {});
    expect(commands()).toMatchObject([
      {
        cmd: {
          type: "content",
          command: {
            type: "addItems",
            items: [{ type: "Video", path: "/clips/a.mp4", overlays: [] }],
            open: true,
          },
        },
      },
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it.each([
    ["a remote server's window", true],
    ["a browser", false],
  ])(
    "asks %s for a path on the server, and says why the backend refused it",
    async (_, desktop) => {
      serve();
      if (desktop) {
        chooses(null);
        // A window on another machine's server is offered no file dialog of this machine's.
        window.axDevilWindow = Object.fromEntries(
          Object.entries(window.axDevilWindow!).filter(([name]) => !name.startsWith("pick")),
        ) as unknown as WindowBridge;
      }
      await renderShell();
      FakeSocket.latest.defer = true;
      fireEvent.keyDown(document, { key: "n", ctrlKey: true });
      expect(screen.queryByRole("button", { name: "Choose video file" })).toBeNull();
      fireEvent.change(screen.getByRole("textbox", { name: "Video path" }), {
        target: { value: " /srv/clips/a.mp4 " },
      });
      await enabled();
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      await act(async () => {});
      expect(commands()).toMatchObject([
        {
          cmd: {
            type: "content",
            command: {
              type: "addItems",
              items: [{ type: "Video", path: "/srv/clips/a.mp4", overlays: [] }],
              open: true,
            },
          },
        },
      ]);
      await act(async () =>
        FakeSocket.latest.emit({
          type: "ack",
          seq: commands()[0]!.seq,
          outcome: { status: "refused", code: "Invalid" },
        }),
      );
      expect(screen.getByRole("dialog", { name: "Add Video" }).textContent).toContain(
        "cannot open",
      );
      // The dialog stays for another try, which closes it once accepted.
      FakeSocket.latest.defer = false;
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      await act(async () => {});
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );
});

describe("add video with an overlay", () => {
  it("asks for the options its decoder declares, sends them as typed, and holds back a missing required one", async () => {
    const options = [
      { key: "width", label: "Image width", kind: { kind: "Integer", min: 1 }, required: true },
    ];
    const decoders = [
      { handler: "MOT_FILE", display_name: "MOT", extensions: [".txt"], options },
      { handler: "ONVIF_XML", display_name: "ONVIF", extensions: [".xml"], options: [] },
    ];
    vi.stubGlobal(
      "fetch",
      async (url: string) =>
        new Response(JSON.stringify(url === "/api/files/check" ? [] : { decoders, resolvers: [] })),
    );
    await renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    fireEvent.change(screen.getByRole("textbox", { name: "Video path" }), {
      target: { value: "/srv/a.mp4" },
    });
    fireEvent.change(await screen.findByRole("textbox", { name: "Overlay path" }), {
      target: { value: "/srv/gt.txt" },
    });
    await enabled();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("alert").textContent).toContain("Image width is required");
    const sent = () => FakeSocket.latest.received.filter((message) => message.type === "command");
    expect(sent()).toEqual([]);

    fireEvent.change(screen.getByRole("textbox", { name: "Image width" }), {
      target: { value: "1920" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await act(async () => {});
    expect(sent()).toMatchObject([
      {
        cmd: {
          command: {
            items: [
              {
                path: "/srv/a.mp4",
                overlays: [{ path: "/srv/gt.txt", handler: "MOT_FILE", options: { width: 1920 } }],
              },
            ],
          },
        },
      },
    ]);
  });
});
