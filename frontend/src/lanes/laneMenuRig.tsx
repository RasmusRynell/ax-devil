import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vite-plus/test";
import { AccessContext } from "../client/access";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import type { CatalogInfoWire, LaneStatusWire } from "../generated/api";
import type { SettingsApi } from "../client/settings";
import type { CadenceWire } from "../stream/protocol";
import { LANE_SETTINGS } from "./fakeOpened";
import { LaneMenu } from "./LaneMenu";
import type { LaneState } from "./lanes";

/** What the lane menu's tests share: a lane of two classes, the catalogs on offer, and a menu rendered over fake settings. */

export const CATALOGS = [
  { id: "built-in:standard", name: "Standard", description: "" },
  { id: "built-in:tracking", name: "Tracking", description: "" },
  { id: "user:night.json", name: "Standard", description: "" },
];

export const READY: Extract<LaneStatusWire, { status: "Ready" }> = {
  status: "Ready",
  bases: ["Frame"],
  default_basis: "Frame",
  filter: [
    { id: "pedestrian", label: "Pedestrian", default_enabled: true },
    { id: "static", label: "Static person", default_enabled: false },
  ],
  warnings: [],
  samples: 12,
  data_revision: 1,
  alignment: null,
  mean_sample_bytes: 100,
  ingest: { state: "Complete" },
};

export const ALL_FEATURES = 0b1_1111_1111;

export interface Backend {
  catalogs?: CatalogInfoWire[];
  /** The Personal setting `overlays.default_catalog`. */
  defaultCatalog?: string;
}

export function setup(
  lane: Partial<LaneState> = {},
  status: LaneStatusWire = READY,
  save: SettingsApi["apply"] = vi.fn(async () => null),
  cadence: CadenceWire | null = null,
  { catalogs = CATALOGS, defaultCatalog = "" }: Backend = {},
) {
  const onChange = vi.fn();
  const onHide = vi.fn();
  const onApplyAll = vi.fn();
  const state: LaneState = {
    label: "DPM",
    slot: "DPM",
    source: 0,
    status,
    settings: LANE_SETTINGS,
    policyRevision: 1,
    pending: false,
    look: { themes: ["night"], features: ALL_FEATURES },
    catalogRevision: 1,
    ...lane,
  };
  const menu = (shown: LaneState) => (
    <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
      <FakeSettings
        apply={save}
        state={{
          ...DEFAULT_SETTINGS,
          values: { ...DEFAULT_SETTINGS.values, "overlays.default_catalog": defaultCatalog },
        }}
      >
        <LaneMenu
          lane={shown}
          catalogs={catalogs}
          cadence={cadence}
          onChange={onChange}
          onHide={onHide}
          onApplyAll={onApplyAll}
        />
      </FakeSettings>
    </AccessContext.Provider>
  );
  const rendered = render(menu(state));
  const show = (change: Partial<LaneState>) => rendered.rerender(menu({ ...state, ...change }));
  return { onChange, onHide, onApplyAll, save, state, show, ...rendered };
}

/** Opens a tab by name; the Filter tab's name carries its count of changed controls. */
export const tab = (name: string) =>
  fireEvent.click(screen.getByRole("tab", { name: new RegExp(`^${name}( \\d+)?$`) }));
export const box = (name: string) => screen.getByRole("checkbox", { name }) as HTMLInputElement;
