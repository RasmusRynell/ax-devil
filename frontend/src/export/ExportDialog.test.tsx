import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import {
  act,
  cleanup,
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { GrantsContext } from "../access";
import type { ClientMessage, ServerMessage } from "../generated/api";
import type { LaneState } from "../playback/contract";
import { LANE_SETTINGS } from "../playback/fakeOpened";
import type { LaneStatusWire } from "../playback/protocol";
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

afterEach(() => {
  cleanup();
  delete window.axDevilWindow;
});

describe("export dialog", () => {
  it("exports the drawable visible lanes as the viewer holds them, to the path typed", () => {
    const { exported } = open();
    expect(screen.queryByLabelText("Hidden")).toBeNull();
    // A lane still loading cannot be drawn.
    expect((screen.getByLabelText("GT") as HTMLInputElement).disabled).toBe(true);
    type("From frame", "10");
    type("To frame", "19");
    fireEvent.change(screen.getByLabelText("Quality"), { target: { value: "high" } });
    expect((screen.getByRole("button", { name: "Export" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    type("Path on the server", "/out/clip.mp4");
    press("Export");
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

  it("offers no lane choice for one lane, which is exported", () => {
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
    type("Path on the server", "/out/clip.mp4");
    press("Export");
    expect(sent).toMatchObject([{ type: "Export", lanes: [{ lane: 0 }] }]);
  });

  it("takes a single lane whatever was unchecked, and says why a single lane failed", () => {
    const ready = { ...VIEW, lanes: [lane("DPM", { source: 0, status: READY })] };
    const jobs = new ExportJobs(() => {});
    const { rerender } = render(
      <ExportDialog jobs={jobs} view={VIEW} name="clip.mp4" only={0} onClose={() => {}} />,
    );
    // GT, unchecked by `only`, is now the only visible lane.
    const one = {
      ...VIEW,
      lanes: [VIEW.lanes[0], lane("GT", { ...VIEW.lanes[2], status: READY })],
    };
    one.lanes[0] = { ...one.lanes[0], settings: { ...LANE_SETTINGS, visible: false } };
    rerender(<ExportDialog jobs={jobs} view={one} name="clip.mp4" only={0} onClose={() => {}} />);
    type("Path on the server", "/out/clip.mp4");
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

  it("starts with just the lane a lane menu was used on checked", () => {
    const both = {
      ...VIEW,
      lanes: [lane("DPM", { source: 0, status: READY }), lane("GT", { source: 1, status: READY })],
    };
    const jobs = new ExportJobs(() => {});
    render(<ExportDialog jobs={jobs} view={both} name="clip.mp4" only={1} onClose={() => {}} />);
    const checked = (name: string) => (screen.getByLabelText(name) as HTMLInputElement).checked;
    expect([checked("DPM"), checked("GT")]).toEqual([false, true]);
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

  it("exports the frame on screen as a PNG", () => {
    const { exported } = open();
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "png" } });
    type("Path on the server", "/out/frame.png");
    press("Export");
    expect(exported()[0]).toMatchObject({ first: 40, last: 40, format: { format: "Png" } });
  });

  it("appends the format's extension to a path that lacks it, and keeps one that has it", () => {
    for (const [typed, saved] of [
      ["/out/clip", "/out/clip.mp4"],
      ["/out/clip.mov", "/out/clip.mov.mp4"],
      ["/out/CLIP.MP4", "/out/CLIP.MP4"],
    ]) {
      const { exported } = open();
      type("Path on the server", typed);
      press("Export");
      expect(exported()[0]).toMatchObject({ path: saved });
      cleanup();
    }
  });

  it("refuses a range outside the video", () => {
    open();
    type("Path on the server", "/out/clip.mp4");
    type("To frame", "100");
    expect((screen.getByRole("button", { name: "Export" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("shows progress, cancels on request and says when the export was cancelled", () => {
    const { sent, jobs } = open();
    type("Path on the server", "/out/clip.mp4");
    press("Export");
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

  it("tells where the file went, or why the export failed, and closes", () => {
    const { jobs, onClose } = open();
    type("Path on the server", "/out/clip.mp4");
    press("Export");
    say(jobs, 1, { state: "Failed", code: "Internal", params: {}, reason: "No encoder." });
    expect(screen.getByRole("alert").textContent).toBe("Error: No encoder.");
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
      <GrantsContext.Provider value={["preferences"]}>
        <ExportDialog jobs={jobs} view={VIEW} name="clip.mp4" onClose={() => {}} />
      </GrantsContext.Provider>,
    );
    expect(screen.queryByLabelText("Path on the server")).toBeNull();
    press("Export");
    await waitFor(() => expect(pick).toHaveBeenCalledWith("clip.mp4", "mp4"));
    expect(sent).toEqual([]);
    press("Export");
    await waitFor(() =>
      expect(sent).toMatchObject([{ type: "Export", path: "/home/me/clip.mp4" }]),
    );
  });
});
