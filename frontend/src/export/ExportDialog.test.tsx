import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import {
  act,
  cleanup,
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { AccessContext } from "../client/access";
import type { ClientMessage, ServerMessage } from "../generated/api";
import type { LaneState } from "../lanes/contract";
import { LANE_SETTINGS } from "../lanes/fakeOpened";
import type { LaneStatusWire } from "../stream/protocol";
import { INITIAL_VIEW, type PlaybackView } from "../playback/view";
import { ExportDialog } from "./ExportDialog";
import { ExportJobs } from "./jobs";

const READY: LaneStatusWire = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [
    { id: "car", label: "car", default_enabled: true },
    { id: "bike", label: "bike", default_enabled: false },
  ],
  warnings: [],
  samples: 5,
  data_revision: 7,
  alignment: null,
  mean_sample_bytes: 10,
  ingest: { state: "Complete" },
};

const lane = (label: string, state: Partial<LaneState>): LaneState => ({
  label,
  slot: label,
  source: null,
  status: "plain",
  settings: LANE_SETTINGS,
  policyRevision: 0,
  pending: false,
  catalogRevision: 3,
  ...state,
});

/** A 100 frame video paused on frame 40, with a drawn lane, a hidden one and one still loading. */
const VIEW: PlaybackView = {
  ...INITIAL_VIEW,
  status: "ready",
  frameCount: 100,
  playhead: 40,
  lanes: [
    lane("DPM", { source: 0, status: READY, policyRevision: 2 }),
    lane("Hidden", { settings: { ...LANE_SETTINGS, visible: false } }),
    lane("GT", { source: 1, status: { status: "Loading" } }),
  ],
};

function open() {
  const sent: ClientMessage[] = [];
  const jobs = new ExportJobs((message) => sent.push(message));
  const onClose = vi.fn();
  render(<ExportDialog jobs={jobs} view={VIEW} name="clip.mp4" onClose={onClose} />);
  const exported = () => sent.filter((message) => message.type === "Export");
  return { sent, jobs, onClose, exported };
}

const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const disabled = (name: string) =>
  (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
/** Escape in the open dialog. */
const escape = () =>
  fireEvent(
    document.querySelector("dialog") as HTMLDialogElement,
    createEvent("cancel", document.querySelector("dialog") as HTMLDialogElement, {
      cancelable: true,
    }),
  );
const say = (
  jobs: ExportJobs,
  job: number,
  state: Extract<ServerMessage, { type: "Export" }>["state"],
) => act(() => jobs.receive({ type: "Export", job, state }));

/** Export once the previous pick has settled: the button is disabled while a dialog is open, and a click then is lost. */
const pressExport = async () => {
  await waitFor(() => expect(disabled("Export")).toBe(false));
  press("Export");
};

/** What the stubbed native dialog gives for the export file. */
let chosen = "";
const exportNow = () =>
  act(async () => {
    press("Export");
  });

beforeEach(() => {
  chosen = "";
  window.axDevilWindow = {
    pickExportFile: () => Promise.resolve(chosen),
  } as unknown as typeof window.axDevilWindow;
});

afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
});

describe("export dialog", () => {
  it("exports the drawable visible lanes as the viewer holds them, to the path chosen", async () => {
    const { exported } = open();
    expect(screen.queryByLabelText("Hidden")).toBeNull();
    // A lane still loading cannot be drawn.
    expect((screen.getByLabelText("GT") as HTMLInputElement).disabled).toBe(true);
    type("From frame", "10");
    type("To frame", "19");
    fireEvent.change(screen.getByLabelText("Quality"), { target: { value: "high" } });
    chosen = "/out/clip.mp4";
    await exportNow();
    expect(exported()).toEqual([
      {
        type: "Export",
        job: 1,
        lanes: [
          {
            lane: 0,
            catalog: { id: "built-in:standard", revision: 3 },
            style: {
              label: "DPM",
              enabled_classes: [0],
              class_options: 2,
              hidden: 0,
              theme: null,
              retained_opacity: 1,
              filter: null,
            },
            policy_revision: 2,
            data_revision: 7,
          },
        ],
        first: 10,
        last: 19,
        format: { format: "Mp4", quality: "high" },
        path: "/out/clip.mp4",
      },
    ]);
  });

  it("says the render catalogs are still loading only while a lane is", () => {
    const message = "Render catalogs are still loading.";
    const { unmount } = render(
      <ExportDialog
        jobs={new ExportJobs(() => {})}
        view={VIEW}
        name="clip.mp4"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(message)).toBeTruthy();
    unmount();
    const warm = { ...VIEW, lanes: [lane("DPM", { source: 0, status: READY })] };
    render(
      <ExportDialog
        jobs={new ExportJobs(() => {})}
        view={warm}
        name="clip.mp4"
        onClose={() => {}}
      />,
    );
    expect(screen.queryByText(message)).toBeNull();
  });

  it("offers no lane choice for one lane, which is exported", async () => {
    const sent: ClientMessage[] = [];
    const view = { ...VIEW, lanes: [lane("DPM", { source: 0, status: READY })] };
    render(
      <ExportDialog
        jobs={new ExportJobs((message) => sent.push(message))}
        view={view}
        name="clip.mp4"
        onClose={() => {}}
      />,
    );
    expect(screen.queryByText("Lanes")).toBeNull();
    chosen = "/out/clip.mp4";
    await exportNow();
    expect(sent).toMatchObject([{ type: "Export", lanes: [{ lane: 0 }] }]);
  });

  it("takes a single lane whatever was unchecked, and says why a single lane failed", () => {
    const ready = { ...VIEW, lanes: [lane("DPM", { source: 0, status: READY })] };
    const jobs = new ExportJobs(() => {});
    const both = {
      ...VIEW,
      lanes: [lane("DPM", { source: 0, status: READY }), lane("GT", { source: 1, status: READY })],
    };
    const { rerender } = render(
      <ExportDialog jobs={jobs} view={both} name="clip.mp4" onClose={() => {}} />,
    );
    fireEvent.click(screen.getByLabelText("GT"));
    // GT, unchecked, is now the only visible lane.
    const one = {
      ...both,
      lanes: [{ ...both.lanes[0], settings: { ...LANE_SETTINGS, visible: false } }, both.lanes[1]],
    };
    rerender(<ExportDialog jobs={jobs} view={one} name="clip.mp4" onClose={() => {}} />);
    expect((screen.getByRole("button", { name: "Export" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    cleanup();

    const failed = {
      ...ready,
      lanes: [
        lane("DPM", {
          source: 0,
          status: { status: "Loading" },
          catalogRevision: undefined,
          catalogProblem: "Catalog broke.",
        }),
      ],
    };
    render(<ExportDialog jobs={jobs} view={failed} name="clip.mp4" onClose={() => {}} />);
    expect(screen.getByRole("alert").textContent).toBe("Catalog broke.");
  });

  it("a lane menu's export offers only that lane, with no checkboxes", async () => {
    const both = {
      ...VIEW,
      lanes: [lane("DPM", { source: 0, status: READY }), lane("GT", { source: 1, status: READY })],
    };
    const sent: ClientMessage[] = [];
    const jobs = new ExportJobs((message) => sent.push(message));
    render(<ExportDialog jobs={jobs} view={both} name="clip.mp4" only={1} onClose={() => {}} />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    chosen = "/out/clip.mp4";
    await exportNow();
    expect(sent).toMatchObject([{ type: "Export", lanes: [{ lane: 1 }] }]);
  });

  it("has no Cancel to press before the export starts, and Escape still closes", () => {
    const { onClose } = open();
    expect(disabled("Cancel")).toBe(true);
    escape();
    expect(onClose).toHaveBeenCalled();
  });

  it("names a long lane in full while showing it elided", () => {
    const name = "front-entrance-detections-2026-03-14";
    const view = {
      ...VIEW,
      lanes: [lane(name, { source: 0, status: READY }), lane("GT", { source: 1, status: READY })],
    };
    render(
      <ExportDialog jobs={new ExportJobs(() => {})} view={view} name="c.mp4" onClose={() => {}} />,
    );
    expect(screen.getByLabelText(name)).toBeTruthy();
    expect(screen.getByTitle(name)).toBeTruthy();
  });

  it("exports the frame on screen as a PNG", async () => {
    const { exported } = open();
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "png" } });
    chosen = "/out/frame.png";
    await exportNow();
    expect(exported()[0]).toMatchObject({ first: 40, last: 40, format: { format: "Png" } });
  });

  it("appends the format's extension to a path that lacks it, and keeps one that has it", async () => {
    for (const [typed, saved] of [
      ["/out/clip", "/out/clip.mp4"],
      ["/out/clip.mov", "/out/clip.mov.mp4"],
      ["/out/CLIP.MP4", "/out/CLIP.MP4"],
    ]) {
      const { exported } = open();
      chosen = typed;
      await exportNow();
      expect(exported()[0]).toMatchObject({ path: saved });
      cleanup();
    }
  });

  it("refuses a range outside the video", () => {
    open();
    type("To frame", "100");
    expect((screen.getByRole("button", { name: "Export" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("shows progress, cancels on request and says when the export was cancelled", async () => {
    const { sent, jobs } = open();
    chosen = "/out/clip.mp4";
    await exportNow();
    say(jobs, 1, { state: "Running", done: 25, total: 100 });
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
    press("Cancel");
    expect(sent.at(-1)).toEqual({ type: "CancelExport", job: 1 });
    say(jobs, 1, { state: "Cancelled" });
    expect(screen.getByRole("status").textContent).toBe("Cancelled");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
    expect(disabled("Cancel")).toBe(true);
    expect(disabled("Close")).toBe(false);
  });

  it("tells where the file went, or why the export failed, and closes", async () => {
    const { jobs, onClose } = open();
    chosen = "/out/clip.mp4";
    await exportNow();
    say(jobs, 1, {
      state: "Failed",
      code: "Internal",
      params: {},
      reason: "Export frame timestamps must increase (timestamp 0 µs)",
    });
    expect(screen.getByRole("alert").textContent).toBe(
      "Error: Export frame timestamps must increase (timestamp 0 µs)",
    );
    expect(disabled("Cancel")).toBe(true);
    press("Close");
    expect(onClose).toHaveBeenCalled();
    expect(jobs.getSnapshot()).toEqual({ phase: "idle" });
  });

  it("asks a local window's native dialog for the file, and exports nothing when it is cancelled", async () => {
    const pick = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce("/home/me/clip.mp4");
    window.axDevilWindow = { pickExportFile: pick } as unknown as typeof window.axDevilWindow;
    const sent: ClientMessage[] = [];
    const jobs = new ExportJobs((message) => sent.push(message));
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
        <ExportDialog jobs={jobs} view={VIEW} name="clip.mp4" onClose={() => {}} />
      </AccessContext.Provider>,
    );
    expect(screen.queryByLabelText("Path on the server")).toBeNull();
    await pressExport();
    await waitFor(() => expect(pick).toHaveBeenCalledWith("DPM.mp4", "mp4", undefined));
    expect(sent).toEqual([]);
    await pressExport();
    await waitFor(() =>
      expect(sent).toMatchObject([{ type: "Export", path: "/home/me/clip.mp4" }]),
    );
  });

  it("suggests the first checked lane's file name, following the selection", async () => {
    const pick = vi.fn().mockResolvedValue(null);
    window.axDevilWindow = { pickExportFile: pick } as unknown as typeof window.axDevilWindow;
    const view = {
      ...VIEW,
      lanes: [
        lane("front.v2.avi", { source: 0, status: READY }),
        lane("detections.json", { source: 1, status: READY }),
        lane("camera.v2/tracks", { source: 2, status: READY }),
        lane("folder/", { source: 3, status: READY }),
      ],
    };
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
        <ExportDialog
          jobs={new ExportJobs(() => {})}
          view={view}
          name="clip.mp4"
          onClose={() => {}}
        />
      </AccessContext.Provider>,
    );
    await pressExport();
    await waitFor(() => expect(pick).toHaveBeenLastCalledWith("front.v2.mp4", "mp4", undefined));
    fireEvent.click(screen.getByLabelText("front.v2.avi"));
    await pressExport();
    await waitFor(() => expect(pick).toHaveBeenLastCalledWith("detections.mp4", "mp4", undefined));
    fireEvent.click(screen.getByLabelText("detections.json"));
    await pressExport();
    await waitFor(() => expect(pick).toHaveBeenLastCalledWith("tracks.mp4", "mp4", undefined));
    fireEvent.click(screen.getByLabelText("camera.v2/tracks"));
    await pressExport();
    await waitFor(() => expect(pick).toHaveBeenLastCalledWith("folder.mp4", "mp4", undefined));
  });
});
