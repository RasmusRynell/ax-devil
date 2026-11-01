import { COMMANDS, type CommandId } from "./generated/settings";
import type { MenuEntry } from "./kit/Menu";

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

/** The menu panel entries for a layout, each command with its label and its key in `shortcuts`. */
export function menuEntries(items: readonly Layout[], shortcuts: Shortcuts): MenuEntry[] {
  return items.map((item): MenuEntry => {
    if (item.kind === "separator") return item;
    if (item.kind === "submenu")
      return {
        kind: "submenu",
        label: item.label,
        items: item.items.length
          ? menuEntries(item.items, shortcuts)
          : [{ kind: "action", id: "none", label: "None", disabled: true }],
      };
    const { label } = command(item.id);
    return {
      kind: item.kind,
      id: item.id,
      label,
      shortcut: shortcuts[item.id] ?? undefined,
    };
  });
}
