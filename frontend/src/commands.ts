import { COMMANDS, type CommandId } from "./generated/settings";
import type { MenuEntry } from "./kit/Menu";

/**
 * What a command needs of the seat, as the backend's `Can` names it: `host` (read the host's files), `content` (change
 * the workspace), `view` (change what this window shows), or `app` (how this app behaves: always allowed).
 */
export type Scope = "host" | "content" | "view" | "app";

/** What the backend knows of a command: its label, default key and whether holding the key repeats it. */
export type Command = (typeof COMMANDS)[number];

/** The command with this id. */
export function command(id: CommandId): Command {
  const found = COMMANDS.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`unknown command ${id}`);
  return found;
}

/** The effective key of every command, as the backend reports it. */
export type Shortcuts = Record<CommandId, string | null>;

/** What each command needs of the seat. */
const SCOPES: Record<CommandId, Scope> = {
  "new-workspace": "content",
  "open-workspace": "host",
  "save-workspace": "content",
  "save-workspace-as": "content",
  "add-video": "host",
  "add-live-stream": "content",
  "add-playlist": "host",
  "keyboard-shortcuts": "app",
  settings: "app",
  exit: "app",
  fullscreen: "app",
  "lane-fullscreen": "view",
  "media-tools": "view",
  sidebar: "view",
  "play-pause": "view",
  "step-forward": "view",
  "step-back": "view",
  "step-forward-10": "view",
  "step-back-10": "view",
  "previous-entry": "view",
  "next-entry": "view",
  "speed-up": "view",
  "speed-down": "view",
  "lane-names": "view",
  "object-details": "view",
  "info-overlay": "view",
  "add-bookmark": "content",
  "export-video": "content",
  "render-catalogs": "content",
  "debug-metrics": "app",
  plugins: "content",
  "collect-metrics": "app",
  "quick-setup": "app",
  about: "app",
};

type Layout =
  | { kind: "separator" }
  | { kind: "action" | "check"; id: CommandId }
  | { kind: "submenu"; label: string; items: readonly Layout[] };

const SEPARATOR: Layout = { kind: "separator" };
const action = (id: CommandId): Layout => ({ kind: "action", id });
const check = (id: CommandId): Layout => ({ kind: "check", id });
const submenu = (label: string, items: readonly Layout[]): Layout => ({
  kind: "submenu",
  label,
  items,
});

/** The menu bar's layout: which commands sit in which menu. Labels, keys and repeat come from the backend. */
export const MENUS: readonly { label: string; items: readonly Layout[] }[] = [
  {
    label: "File",
    items: [
      action("new-workspace"),
      action("open-workspace"),
      submenu("Open Recent", []),
      action("save-workspace"),
      action("save-workspace-as"),
      SEPARATOR,
      action("add-video"),
      action("add-live-stream"),
      action("add-playlist"),
      SEPARATOR,
      action("keyboard-shortcuts"),
      action("settings"),
      SEPARATOR,
      action("exit"),
    ],
  },
  {
    label: "View",
    items: [
      action("fullscreen"),
      action("lane-fullscreen"),
      action("media-tools"),
      action("sidebar"),
      submenu("Playback", [
        action("play-pause"),
        SEPARATOR,
        action("step-forward"),
        action("step-back"),
        action("step-forward-10"),
        action("step-back-10"),
        SEPARATOR,
        action("speed-up"),
        action("speed-down"),
      ]),
      SEPARATOR,
      check("lane-names"),
      check("object-details"),
      action("info-overlay"),
      SEPARATOR,
      action("add-bookmark"),
      action("export-video"),
      SEPARATOR,
      action("render-catalogs"),
    ],
  },
  {
    label: "Debug",
    items: [action("debug-metrics"), action("plugins"), SEPARATOR, check("collect-metrics")],
  },
  { label: "Help", items: [action("quick-setup"), action("about")] },
];

/** The scope of the command `id`; an unknown id is a programming error. */
export function scopeOf(id: string): Scope {
  const found = (SCOPES as Record<string, Scope | undefined>)[id];
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
 * The menus as this window shows them: only the `wired` commands (those that do something today), each with its
 * label and its key in `shortcuts`, and disabled outside the `allowed` scopes. A menu left with nothing drops out.
 */
export function menusFor(
  allowed: Record<Scope, boolean>,
  wired: ReadonlySet<string>,
  shortcuts: Shortcuts,
): { label: string; entries: MenuEntry[] }[] {
  const build = (items: readonly Layout[]): MenuEntry[] =>
    tidy(
      items.flatMap((item): MenuEntry[] => {
        if (item.kind === "separator") return [item];
        if (item.kind === "submenu") {
          const entries = build(item.items);
          return entries.length > 0 ? [{ kind: "submenu", label: item.label, items: entries }] : [];
        }
        if (!wired.has(item.id)) return [];
        return [
          {
            kind: item.kind,
            id: item.id,
            label: command(item.id).label,
            shortcut: shortcuts[item.id] ?? undefined,
            disabled: !allowed[SCOPES[item.id]],
          },
        ];
      }),
    );
  return MENUS.map((menu) => ({ label: menu.label, entries: build(menu.items) })).filter(
    (menu) => menu.entries.length > 0,
  );
}
