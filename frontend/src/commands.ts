import type { MenuCommand, MenuEntry } from "./kit/Menu";

/**
 * What a command changes: `content` (the workspace and what its sources show: only the owner or a private window),
 * `view` (what this window shows of it: also an exploring watcher) or `app` (how this app behaves: always).
 */
export type Scope = "content" | "view" | "app";

const SEPARATOR: MenuEntry = { kind: "separator" };
const scopes = new Map<string, Scope>();
const entry =
  (kind: "action" | "check") =>
  (scope: Scope, id: string, label: string, shortcut?: string, repeats = false) => {
    scopes.set(id, scope);
    return { kind, id, label, shortcut, repeats } satisfies MenuEntry;
  };
const action = entry("action");
const check = entry("check");

/**
 * The menu bar and every command's label and shortcut, in one table. The shortcut handling and the later shortcuts
 * dialog read the same table, so a key is never defined twice.
 */
export const MENUS: readonly { label: string; entries: readonly MenuEntry[] }[] = [
  {
    label: "File",
    entries: [
      action("content", "new-workspace", "New Workspace"),
      action("content", "open-workspace", "Open Workspace", "Ctrl+O"),
      {
        kind: "submenu",
        label: "Open Recent",
        items: [{ ...action("content", "recent-none", "None"), disabled: true }],
      },
      action("content", "save-workspace", "Save Workspace", "Ctrl+S"),
      action("content", "save-workspace-as", "Save Workspace As", "Ctrl+Shift+S"),
      SEPARATOR,
      action("content", "add-video", "Add Video", "Ctrl+N"),
      action("content", "add-live-stream", "Add Live Stream", "Ctrl+L"),
      action("content", "add-playlist", "Add Playlist", "Ctrl+Shift+N"),
      SEPARATOR,
      action("app", "keyboard-shortcuts", "Keyboard Shortcuts", "Ctrl+K"),
      action("app", "settings", "Settings", "Ctrl+,"),
      SEPARATOR,
      action("app", "exit", "Exit", "Ctrl+Q"),
    ],
  },
  {
    label: "View",
    entries: [
      action("app", "fullscreen", "Toggle Fullscreen", "F11"),
      action("view", "lane-fullscreen", "Toggle Lane Fullscreen", "F"),
      action("view", "media-tools", "Media Tools Panel", "Ctrl+B"),
      action("view", "sidebar", "Sidebar", "Ctrl+\\"),
      {
        kind: "submenu",
        label: "Playback",
        items: [
          action("view", "play-pause", "Play / Pause", "Space"),
          SEPARATOR,
          action("view", "step-forward", "Step Forward 1 Frame", ".", true),
          action("view", "step-back", "Step Back 1 Frame", ",", true),
          action("view", "step-forward-10", "Step Forward 10 Frames", "Right", true),
          action("view", "step-back-10", "Step Back 10 Frames", "Left", true),
          SEPARATOR,
          action("view", "speed-up", "Playback Speed Up", "]", true),
          action("view", "speed-down", "Playback Speed Down", "[", true),
        ],
      },
      SEPARATOR,
      check("view", "lane-names", "Show Lane Names"),
      check("view", "object-details", "Object Hover and Click Details"),
      action("view", "info-overlay", "Toggle Info Overlay", "I"),
      SEPARATOR,
      action("content", "add-bookmark", "Add Bookmark", "M"),
      action("content", "export-video", "Export Video", "Ctrl+E"),
      SEPARATOR,
      action("content", "render-catalogs", "Render Catalogs", "Ctrl+R"),
    ],
  },
  {
    label: "Debug",
    entries: [
      action("app", "debug-metrics", "Debug Metrics", "Ctrl+D"),
      action("content", "plugins", "Plugins"),
      SEPARATOR,
      check("app", "collect-metrics", "Collect Debug Metrics"),
    ],
  },
  {
    label: "Help",
    entries: [action("app", "quick-setup", "Quick Setup"), action("app", "about", "About")],
  },
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

/** The scope of the command `id`; an unknown id is a programming error. */
export function scopeOf(id: string): Scope {
  const found = scopes.get(id);
  if (!found) throw new Error(`unknown command ${id}`);
  return found;
}

/** Drops a separator that would lead, trail or follow another. */
function tidy(entries: readonly MenuEntry[]): MenuEntry[] {
  const kept: MenuEntry[] = [];
  for (const item of entries) {
    const last = kept.at(-1);
    if (item.kind !== "separator" || (last && last.kind !== "separator")) kept.push(item);
  }
  if (kept.at(-1)?.kind === "separator") kept.pop();
  return kept;
}

/**
 * The menus as this window shows them: only the `wired` commands (those that do something today), each disabled
 * outside the `allowed` scopes. A menu left with nothing drops out.
 */
export function menusFor(
  allowed: Record<Scope, boolean>,
  wired: ReadonlySet<string>,
): typeof MENUS {
  const restrict = (entries: readonly MenuEntry[]): MenuEntry[] =>
    tidy(
      entries.flatMap((item): MenuEntry[] => {
        if (item.kind === "separator") return [item];
        if (item.kind === "submenu") {
          const items = restrict(item.items);
          return items.length > 0 ? [{ ...item, items }] : [];
        }
        if (!wired.has(item.id)) return [];
        return [allowed[scopeOf(item.id)] ? item : { ...item, disabled: true }];
      }),
    );
  return MENUS.map((menu) => ({ ...menu, entries: restrict(menu.entries) })).filter(
    (menu) => menu.entries.length > 0,
  );
}
