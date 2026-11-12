// The desktop smoke scenarios, by group. Pure data, one scenario per line.
// - Group and scenario names are lowercase words joined by hyphens.
// - A group is one fresh app launch; key order is the order the groups launch in.
// - Order inside a group is the state each scenario inherits from the one before: add a scenario after the one whose
//   state it needs, or at the end of `workspace` if it needs none.
// - A scenario is `desktop/smoke-<name>.cjs` exporting `run(ctx)`; it must not call Electron APIs at module load (the launcher loads it with a stub that throws on any use).
// oxfmt-ignore
module.exports = {
  rtsp: [
    "loopback",
  ],
  native: ["native-window", "restored-bounds", "hover-window"],
  chrome: [
    "appearance",
    "shell",
    "native-input",
    "live",
    "gallery",
    "remote",
    "handoff",
  ],
  media: [
    "video",
    "export",
    "bookmarks",
    "playlist",
    "split",
    "rows",
    "sharing",
  ],
  workspace: [
    "plugins",
    "items",
    "sidebar",
  ],
  backends: [
    "pixels",
    "reload",
    "livestreams",
    "twice",
    "fields",
    "events",
  ],
};
