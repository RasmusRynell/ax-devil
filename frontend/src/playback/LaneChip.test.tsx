import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSettings } from "../fakeSettings";
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
      render(<LaneChip lane={lane(change)} catalogs={[]} cadence={null} onChange={() => {}} />);
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

  it("lists why a file stopped being read among the lane's notices", () => {
    const stopped: LaneStatusWire = {
      ...READY,
      ingest: {
        state: "Incomplete",
        code: "OverlayInvalid",
        params: {},
        reason: "The converter stopped with exit code 3",
      },
    };
    render(
      <LaneChip
        lane={lane({ status: stopped })}
        catalogs={[]}
        cadence={null}
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "DET: warnings" }));
    expect(screen.getByRole("dialog", { name: "DET: warnings" }).textContent).toContain(
      "The converter stopped with exit code 3",
    );
  });

  it("copies the lane's settings to the others from its menu, which closes", () => {
    const onApplyAll = vi.fn();
    render(
      <FakeSettings>
        <LaneChip
          lane={lane({ status: READY })}
          catalogs={[]}
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
      <LaneChip
        lane={lane({
          status: READY,
          catalogProblem: "/layers/0 (3:5): unknown mark\n/rules/1: no field",
        })}
        catalogs={[]}
        cadence={null}
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "DET: warnings" }));
    const panel = screen.getByRole("dialog", { name: "DET: warnings" });
    expect(panel.textContent).toContain("/layers/0 (3:5): unknown mark");
    expect(panel.textContent).toContain("/rules/1: no field");
  });
});
