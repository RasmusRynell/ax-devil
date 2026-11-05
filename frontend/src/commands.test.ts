import { describe, expect, it } from "vite-plus/test";
import { COMMANDS, SETTINGS } from "./generated/settings";
import { menusFor, type Scope } from "./commands";
import { RECENT_ID } from "./shell/recents";

const allowed: Record<Scope, boolean> = { host: true, content: true, view: true, app: true };
const none = Object.fromEntries(COMMANDS.map(({ id }) => [id, null])) as never;

const entries = (menu: string, wired: string[], recents: { path: string; name: string }[] = []) =>
  menusFor(allowed, new Set(wired), none, recents as never).find((m) => m.label === menu)!.entries;

describe("menu tips", () => {
  it("lists a recent workspace by name and tips its full path", () => {
    const path = "/home/me/work/site-a.axdw";
    const [open] = entries("File", ["open-workspace"], [{ path, name: "site-a.axdw" }]).filter(
      (entry) => entry.kind === "submenu",
    );
    expect(open?.kind === "submenu" && open.items).toMatchObject([
      { id: `${RECENT_ID}${path}`, tip: path },
    ]);
  });

  it("tips the overlay toggles with the description their settings show", () => {
    const description = (key: string) => SETTINGS.find((row) => row.key === key)?.description;
    const view = entries("View", ["lane-names", "object-details"]);
    expect(view).toMatchObject([
      { id: "lane-names", tip: description("overlays.lane_names") },
      { id: "object-details", tip: description("overlays.object_details") },
    ]);
    expect(view.every((entry) => entry.kind !== "action" || !entry.tip)).toBe(true);
  });
});
