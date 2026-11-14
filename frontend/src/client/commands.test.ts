import { describe, expect, it } from "vite-plus/test";
import { COMMANDS, SETTINGS } from "../generated/settings";
import { menusFor, scopeOf, type Scope } from "./commands";
import { RECENT_ID } from "./recents";

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
    expect(view.filter((entry) => entry.kind === "check")).toMatchObject([
      { id: "lane-names", tip: description("overlays.lane_names") },
      { id: "object-details", tip: description("overlays.object_details") },
    ]);
    expect(view.every((entry) => entry.kind !== "action" || !entry.tip)).toBe(true);
  });
});

it("keeps unavailable viewer commands visible and disables them until a permitted target offers them", () => {
  const view = entries("View", []);
  expect(view).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: "zoom-in", disabled: true }),
      expect.objectContaining({ id: "add-bookmark", disabled: true }),
      expect.objectContaining({ id: "export-video", disabled: true }),
      expect.objectContaining({
        label: "Playback",
        kind: "submenu",
        items: expect.arrayContaining([
          expect.objectContaining({ id: "play-pause", disabled: true }),
        ]),
      }),
    ]),
  );
  expect(entries("View", ["zoom-in"])).toContainEqual(
    expect.objectContaining({ id: "zoom-in", disabled: false }),
  );
  expect(
    menusFor({ ...allowed, view: false }, new Set(["zoom-in"]), none).find(
      (menu) => menu.label === "View",
    )?.entries,
  ).toContainEqual(expect.objectContaining({ id: "zoom-in", disabled: true }));
});

it("lists plugin commands in a Plugins menu before Help, changing the workspace like a plugin does", () => {
  const plugins = [{ id: "acme.export", title: "Export", on: "lane", runs: "export" }];
  const shortcuts = { "acme.export": "Ctrl+Alt+E" };
  const labels = (list: typeof plugins) =>
    menusFor(allowed, new Set(["about"]), shortcuts, [], list).map((menu) => menu.label);
  expect(labels([])).not.toContain("Plugins");
  expect(labels(plugins).slice(-2)).toEqual(["Plugins", "Help"]);
  const menu = menusFor({ ...allowed, content: false }, new Set(), shortcuts, [], plugins).find(
    (found) => found.label === "Plugins",
  );
  expect(menu?.entries).toEqual([
    { kind: "action", id: "acme.export", label: "Export", shortcut: "Ctrl+Alt+E", disabled: true },
  ]);
  expect(scopeOf("acme.export")).toBe("content");
});
