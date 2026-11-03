import type { ReactNode } from "react";
import type { SettingsState } from "./generated/api";
import { COMMANDS } from "./generated/settings";
import { SettingsContext, type SettingsApi } from "./settings";

/** The state of a backend with no changes: every default, every command on its default key. */
export const DEFAULT_SETTINGS: SettingsState = {
  values: {
    "ui.theme": "auto",
    "ui.text_size": "system",
    "ui.quick_setup_done": true,
    "overlays.object_details": true,
    "overlays.lane_names": true,
    "overlays.default_catalog": "",
    "defaults.device.protocol": "https",
    "playback.video_cache": "auto",
    "storage.cache_dir": "",
    "storage.catalog_dir": "",
  },
  shortcuts: Object.fromEntries(
    COMMANDS.map((command) => [command.id, command.key]),
  ) as SettingsState["shortcuts"],
};

/** Provides settings to a component under test without a backend. */
export function FakeSettings({
  state = DEFAULT_SETTINGS,
  save = () => Promise.resolve(null),
  children,
}: {
  state?: SettingsState;
  save?: SettingsApi["save"];
  children: ReactNode;
}) {
  return <SettingsContext.Provider value={{ ...state, save }}>{children}</SettingsContext.Provider>;
}
