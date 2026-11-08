import { command, scopeOf, type Scope, type Shortcuts } from "../commands";
import type { CommandId } from "../generated/settings";
import type { MenuEntry } from "../kit/Menu";

/** What a right click on a recording's lane offers, in order; each is a command, so labels and keys match the menus. */
export const OFFLINE_LANE_COMMANDS: readonly CommandId[] = [
  "export-video",
  "media-tools",
  "lane-fullscreen",
  "add-bookmark",
  "zoom-reset",
];

/** What a live stream's lane offers: a stream has nothing to export and no frame to bookmark. */
export const LIVE_LANE_COMMANDS: readonly CommandId[] = [
  "media-tools",
  "lane-fullscreen",
  "zoom-reset",
];

/** What Hide tells in its tooltip, wherever it is offered. */
export const HIDE_LANE_TIP =
  "Skip this lane in playback. Bring it back with Unhide in the sidebar.";

/** The entry that hides the clicked lane; it is no command, so it has no key. */
export const HIDE_LANE = "hide-lane";

/** What Export video tells in its tooltip once the clicked lane can be drawn. */
export const EXPORT_TIP = "Export video with overlays";

/** Why Export video is disabled while the clicked lane's render catalog has not loaded. */
export const EXPORT_WAITS = "Export is available once the render catalog has loaded";

/**
 * The lane menu's entries for the `commands` the viewer handles, disabled outside the `allowed` scopes; Hide only when the seat may hide lanes. Export
 * video also waits while the clicked lane is `cold`.
 */
export function laneContextEntries(
  commands: readonly CommandId[],
  allowed: Record<Scope, boolean>,
  shortcuts: Partial<Shortcuts>,
  canHide: boolean,
  cold: boolean,
): MenuEntry[] {
  const entries: MenuEntry[] = commands.map((id) => {
    const waits = id === "export-video" && cold;
    return {
      kind: "action",
      id,
      label: id === "export-video" ? "Export video" : command(id).label,
      shortcut: shortcuts[id] ?? undefined,
      disabled: waits || !allowed[scopeOf(id)],
      reason: waits ? EXPORT_WAITS : undefined,
      tip: id === "export-video" ? EXPORT_TIP : undefined,
    };
  });
  return canHide
    ? [
        ...entries,
        { kind: "separator" },
        { kind: "action", id: HIDE_LANE, label: "Hide", tip: HIDE_LANE_TIP },
      ]
    : entries;
}
