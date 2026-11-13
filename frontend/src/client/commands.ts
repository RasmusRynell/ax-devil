import { COMMANDS, type CommandId } from "../generated/settings";
import type { RecentWorkspace } from "../generated/api";
import type { MenuEntry } from "../kit/Menu";
import { RECENT_ID, recentLabel } from "./recents";

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
  "new-workspace": "host",
  "open-workspace": "host",
  "save-workspace": "host",
  "save-workspace-as": "host",
  "add-video": "host",
  "add-live-stream": "content",
  "add-playlist": "host",
  "keyboard-shortcuts": "app",
  settings: "app",
  exit: "app",
  fullscreen: "app",
  "lane-fullscreen": "view",
  "zoom-in": "view",
  "zoom-out": "view",
  "zoom-reset": "view",
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
  "export-video": "host",
  // The Plugins button of the activity bar is a view like any other, so the command asks the same of the seat.
  plugins: "view",
  "quick-setup": "app",
  about: "app",
};

type Layout =
  | { kind: "separator" }
  | { kind: "action" | "check"; id: CommandId; viewer?: boolean }
  | { kind: "recents" }
  | { kind: "submenu"; label: string; items: readonly Layout[] };

const SEPARATOR: Layout = { kind: "separator" };
const action = (id: CommandId): Layout => ({ kind: "action", id });
const viewer = (id: CommandId): Layout => ({ kind: "action", id, viewer: true });
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
      { kind: "recents" },
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
      check("fullscreen"),
      viewer("lane-fullscreen"),
      viewer("media-tools"),
      check("sidebar"),
      SEPARATOR,
      viewer("zoom-in"),
      viewer("zoom-out"),
      viewer("zoom-reset"),
      SEPARATOR,
      submenu("Playback", [
        viewer("play-pause"),
        SEPARATOR,
        viewer("step-forward"),
        viewer("step-back"),
        viewer("step-forward-10"),
        viewer("step-back-10"),
        SEPARATOR,
        viewer("speed-up"),
        viewer("speed-down"),
      ]),
      SEPARATOR,
      check("lane-names"),
      check("object-details"),
      viewer("info-overlay"),
      SEPARATOR,
      viewer("add-bookmark"),
      viewer("export-video"),
      SEPARATOR,
      action("plugins"),
    ],
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
 * The menus as this window shows them: wired app commands and implemented viewer commands, each with its label
 * and key. Viewer commands without a target stay visible but disabled, as do commands outside allowed scopes.
 * Open Recent lists `recents` and comes with Open Workspace.
 */
export function menusFor(
  allowed: Record<Scope, boolean>,
  wired: ReadonlySet<string>,
  shortcuts: Shortcuts,
  recents: readonly RecentWorkspace[] = [],
): { label: string; entries: MenuEntry[] }[] {
  const build = (items: readonly Layout[]): MenuEntry[] =>
    tidy(
      items.flatMap((item): MenuEntry[] => {
        if (item.kind === "separator") return [item];
        if (item.kind === "submenu") {
          const entries = build(item.items);
          return entries.length > 0 ? [{ kind: "submenu", label: item.label, items: entries }] : [];
        }
        if (item.kind === "recents") {
          if (!wired.has("open-workspace")) return [];
          const entries: MenuEntry[] = recents.map(({ path, name }) => ({
            kind: "action",
            id: `${RECENT_ID}${path}`,
            label: recentLabel(name),
            tip: path,
            disabled: !allowed.host,
          }));
          return [
            {
              kind: "submenu",
              label: "Open Recent",
              items: entries.length
                ? entries
                : [{ kind: "action", id: "recent-none", label: "None", disabled: true }],
            },
          ];
        }
        if (!wired.has(item.id) && !item.viewer) return [];
        return [
          {
            kind: item.kind,
            id: item.id,
            label: command(item.id).label,
            tip: command(item.id).description ?? undefined,
            shortcut: shortcuts[item.id] ?? undefined,
            disabled: !wired.has(item.id) || !allowed[SCOPES[item.id]],
          },
        ];
      }),
    );
  return MENUS.map((menu) => ({ label: menu.label, entries: build(menu.items) })).filter(
    (menu) => menu.entries.length > 0,
  );
}
