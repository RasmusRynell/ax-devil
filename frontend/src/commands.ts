import type { MenuCommand, MenuEntry } from "./kit/Menu";

const SEPARATOR: MenuEntry = { kind: "separator" };
const action = (id: string, label: string, shortcut?: string): MenuEntry => ({
  kind: "action",
  id,
  label,
  shortcut,
});
const check = (id: string, label: string, shortcut?: string): MenuEntry => ({
  kind: "check",
  id,
  label,
  shortcut,
});

/**
 * The menu bar and every command's label and shortcut, in one table. The shortcut handling and the later shortcuts
 * dialog read the same table, so a key is never defined twice.
 */
export const MENUS: readonly { label: string; entries: readonly MenuEntry[] }[] = [
  {
    label: "File",
    entries: [
      action("new-workspace", "New Workspace"),
      action("open-workspace", "Open Workspace", "Ctrl+O"),
      {
        kind: "submenu",
        label: "Open Recent",
        items: [{ kind: "action", id: "recent-none", label: "None", disabled: true }],
      },
      action("save-workspace", "Save Workspace", "Ctrl+S"),
      action("save-workspace-as", "Save Workspace As", "Ctrl+Shift+S"),
      SEPARATOR,
      action("add-video", "Add Video", "Ctrl+N"),
      action("add-live-stream", "Add Live Stream", "Ctrl+L"),
      action("add-playlist", "Add Playlist", "Ctrl+Shift+N"),
      SEPARATOR,
      action("keyboard-shortcuts", "Keyboard Shortcuts", "Ctrl+K"),
      action("settings", "Settings", "Ctrl+,"),
      SEPARATOR,
      action("exit", "Exit", "Ctrl+Q"),
    ],
  },
  {
    label: "View",
    entries: [
      action("fullscreen", "Toggle Fullscreen", "F11"),
      action("lane-fullscreen", "Toggle Lane Fullscreen", "F"),
      action("media-tools", "Media Tools Panel", "Ctrl+B"),
      action("sidebar", "Sidebar", "Ctrl+\\"),
      SEPARATOR,
      check("lane-names", "Show Lane Names"),
      check("object-details", "Object Hover and Click Details"),
      action("info-overlay", "Toggle Info Overlay", "I"),
      SEPARATOR,
      action("add-bookmark", "Add Bookmark", "M"),
      action("export-video", "Export Video", "Ctrl+E"),
      SEPARATOR,
      action("render-catalogs", "Render Catalogs", "Ctrl+R"),
    ],
  },
  {
    label: "Debug",
    entries: [
      action("debug-metrics", "Debug Metrics", "Ctrl+D"),
      action("plugins", "Plugins"),
      SEPARATOR,
      check("collect-metrics", "Collect Debug Metrics"),
    ],
  },
  { label: "Help", entries: [action("quick-setup", "Quick Setup"), action("about", "About")] },
];

function flatten(entries: readonly MenuEntry[]): MenuCommand[] {
  return entries.flatMap((entry) =>
    entry.kind === "separator" ? [] : entry.kind === "submenu" ? flatten(entry.items) : [entry],
  );
}

/** Every command by id. */
export const COMMANDS: ReadonlyMap<string, MenuCommand> = new Map(
  MENUS.flatMap((menu) => flatten(menu.entries)).map((command) => [command.id, command]),
);

/** The command with this id; an unknown id is a programming error. */
export function command(id: string): MenuCommand {
  const found = COMMANDS.get(id);
  if (!found) throw new Error(`unknown command ${id}`);
  return found;
}
