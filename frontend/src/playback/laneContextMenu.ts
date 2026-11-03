import { command, scopeOf, type Scope, type Shortcuts } from "../commands";
import type { CommandId } from "../generated/settings";
import type { MenuEntry } from "../kit/Menu";

/** What a right click on a lane's picture offers, in order; each is a command, so labels and keys match the menus. */
const LANE_COMMANDS: readonly CommandId[] = [
  "export-video",
  "media-tools",
  "lane-fullscreen",
  "zoom-reset",
];

/** What Hide lane tells in its tooltip, wherever it is offered. */
export const HIDE_LANE_TIP =
  "Skip this lane in playback. Bring it back with Unhide in the sidebar.";

/** The entry that hides the clicked lane; it is no command, so it has no key. */
export const HIDE_LANE = "hide-lane";

/** The lane menu's entries, disabled outside the `allowed` scopes; Hide only when the seat may hide lanes. */
export function laneContextEntries(
  allowed: Record<Scope, boolean>,
  shortcuts: Partial<Shortcuts>,
  canHide: boolean,
): MenuEntry[] {
  const entries: MenuEntry[] = LANE_COMMANDS.map((id) => ({
    kind: "action",
    id,
    label: command(id).label,
    shortcut: shortcuts[id] ?? undefined,
    disabled: !allowed[scopeOf(id)],
  }));
  return canHide
    ? [
        ...entries,
        { kind: "separator" },
        { kind: "action", id: HIDE_LANE, label: "Hide lane", tip: HIDE_LANE_TIP },
      ]
    : entries;
}
