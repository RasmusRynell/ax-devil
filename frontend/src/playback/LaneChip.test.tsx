import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { LaneStatusWire } from "../generated/api";
import { LANE_SETTINGS } from "./fakeOpened";
import { LaneChip } from "./LaneChip";
import type { LaneState } from "./lanes";

afterEach(cleanup);

const lane = (change: Partial<LaneState>): LaneState => ({
  label: "DET",
  slot: "DET",
  source: 0,
  status: { status: "Loading" },
  settings: LANE_SETTINGS,
  policyRevision: 1,
  pending: false,
  catalogRevision: 1,
  ...change,
});

const READY: LaneStatusWire = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [],
  warnings: [],
  samples: 1,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 1,
  ingest: { state: "Complete" },
};

describe("lane chip", () => {
  it("says loading while the backend loads the lane and while the shown frame waits for its data", () => {
    const show = (change: Partial<LaneState>) => {
      cleanup();
      render(
        <FakeSettings>
          <LaneChip lane={lane(change)} catalogs={[]} cadence={null} onChange={() => {}} />
        </FakeSettings>,
      );
      return screen.queryByRole("status");
    };
    expect(show({})?.textContent).toBe("Loading…");
    expect(show({ status: READY })).toBeNull();
    expect(show({ status: READY, pending: true })).not.toBeNull();
    // A file still being read shows loading only for frames that wait for it.
    const reading: LaneStatusWire = { ...READY, ingest: { state: "Reading" } };
    expect(show({ status: reading })).toBeNull();
    expect(show({ status: reading, pending: true })?.textContent).toBe("Loading…");
  });

  it("names the overlay it parses until the file is read, with the lane's place when several lanes are shown", () => {
    const show = (change: Partial<LaneState>, stage = { position: 1, count: 1 }, live = false) => {
      cleanup();
      render(
        <FakeSettings>
          <LaneChip
            lane={lane(change)}
            catalogs={[]}
            cadence={null}
            live={live}
            stage={stage}
            onChange={() => {}}
          />
        </FakeSettings>,
      );
      return screen.queryByRole("status");
    };
    expect(show({})?.textContent).toBe("Parsing overlay data: DET");
    expect(show({}, { position: 2, count: 3 })?.textContent).toBe(
      "Parsing overlay data (2/3): DET",
    );
    expect(show({}, { position: 2, count: 3 })?.getAttribute("title")).toBe(
      "Parsing overlay data (2/3): DET",
    );
    expect(show({}, { position: 2, count: 3 }, true)?.textContent).toBe("Loading…");
    // The first publication makes the lane Ready while the file is still being read.
    const reading: LaneStatusWire = { ...READY, ingest: { state: "Reading" } };
    expect(show({ status: reading })?.textContent).toBe("Parsing overlay data: DET");
    expect(show({ status: reading, pending: true })?.textContent).toBe("Parsing overlay data: DET");
    // Complete, Incomplete and Failed end it.
    expect(show({ status: READY })).toBeNull();
    const incomplete: LaneStatusWire = {
      ...READY,
      ingest: { state: "Incomplete", code: "OverlayInvalid", params: {}, reason: "Stopped" },
    };
    expect(show({ status: incomplete })).toBeNull();
    const failed: LaneStatusWire = {
      status: "Failed",
      code: "OverlayInvalid",
      params: {},
      reason: "Unreadable",
    };
    expect(show({ status: failed })).toBeNull();
  });

  it("lists why a file stopped being read among the lane's notices", () => {
    const stopped: LaneStatusWire = {
      ...READY,
      ingest: {
        state: "Incomplete",
        code: "OverlayInvalid",
        params: {},
        reason: "The plugin stopped with exit code 3",
      },
    };
    render(
      <FakeSettings>
        <LaneChip
          lane={lane({ status: stopped })}
          catalogs={[]}
          cadence={null}
          onChange={() => {}}
        />
      </FakeSettings>,
    );
    fireEvent.click(screen.getByRole("button", { name: "DET: warnings" }));
    expect(screen.getByRole("dialog", { name: "DET: warnings" }).textContent).toContain(
      "The plugin stopped with exit code 3",
    );
  });

  it("copies the lane's settings to the others from its menu, which closes", () => {
    const onApplyAll = vi.fn();
    render(
      <FakeSettings>
        <LaneChip
          lane={lane({ status: READY })}
          catalogs={[{ id: LANE_SETTINGS.catalog, name: "Standard", description: "" }]}
          cadence={null}
          onChange={() => {}}
          onApplyAll={onApplyAll}
        />
      </FakeSettings>,
    );
    fireEvent.click(screen.getByRole("button", { name: "DET: settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply to all lanes" }));
    expect(onApplyAll).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists why the chosen catalog is not used among the lane's notices", () => {
    render(
      <FakeSettings>
        <LaneChip
          lane={lane({
            status: READY,
            catalogProblem: "/layers/0 (3:5): unknown mark\n/rules/1: no field",
          })}
          catalogs={[]}
          cadence={null}
          onChange={() => {}}
        />
      </FakeSettings>,
    );
    fireEvent.click(screen.getByRole("button", { name: "DET: warnings" }));
    const panel = screen.getByRole("dialog", { name: "DET: warnings" });
    expect(panel.textContent).toContain("/layers/0 (3:5): unknown mark");
    expect(panel.textContent).toContain("/rules/1: no field");
  });

  it("drops the name but keeps the lane's controls when Show Lane Names is off", () => {
    const chip = (names: boolean) => (
      <FakeSettings
        state={{
          ...DEFAULT_SETTINGS,
          values: { ...DEFAULT_SETTINGS.values, "overlays.lane_names": names },
        }}
      >
        <LaneChip lane={lane({ status: READY })} catalogs={[]} cadence={null} onChange={() => {}} />
      </FakeSettings>
    );
    const { unmount } = render(chip(true));
    expect(screen.queryByText("DET")).not.toBeNull();
    unmount();
    render(chip(false));
    expect(screen.queryByText("DET")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "DET: settings" }));
    expect(screen.getByRole("dialog", { name: "DET: settings" })).toBeTruthy();
  });
});
