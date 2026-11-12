import type { ReactNode } from "react";
import type { SettingsState } from "../generated/api";
import { COMMANDS } from "../generated/settings";
import { DEFAULT_VALUES, SettingsContext, through, type SettingsApi } from "./settings";

/** The state of a backend with no changes: every default, every command on its default key. */
export const DEFAULT_SETTINGS: SettingsState = {
  revision: 0,
  values: { ...DEFAULT_VALUES, "ui.quick_setup_done": true },
  shortcuts: Object.fromEntries(
    COMMANDS.map((command) => [command.id, command.key]),
  ) as SettingsState["shortcuts"],
};

/** Provides settings to a component under test without a backend. */
export function FakeSettings({
  state = DEFAULT_SETTINGS,
  apply = () => Promise.resolve(null),
  children,
}: {
  state?: SettingsState;
  apply?: SettingsApi["apply"];
  children: ReactNode;
}) {
  return (
    <SettingsContext.Provider value={{ ...state, ...through(apply) }}>
      {children}
    </SettingsContext.Provider>
  );
}
