import type { ReactNode } from "react";
import type { SettingsState } from "./generated/api";
import { COMMANDS } from "./generated/settings";
import { DEFAULT_VALUES, SettingsContext, type SettingsApi } from "./settings";

/** The state of a backend with no changes: every default, every command on its default key. */
export const DEFAULT_SETTINGS: SettingsState = {
  values: { ...DEFAULT_VALUES, "ui.quick_setup_done": true },
  shortcuts: Object.fromEntries(
    COMMANDS.map((command) => [command.id, command.key]),
  ) as SettingsState["shortcuts"],
};

/** Provides settings to a component under test without a backend. */
export function FakeSettings({
  state = DEFAULT_SETTINGS,
  save = () => Promise.resolve(null),
  reload = () => Promise.resolve(),
  children,
}: {
  state?: SettingsState;
  save?: SettingsApi["save"];
  reload?: SettingsApi["reload"];
  children: ReactNode;
}) {
  return (
    <SettingsContext.Provider value={{ ...state, save, reload }}>
      {children}
    </SettingsContext.Provider>
  );
}
