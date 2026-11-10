// The desktop smoke scenarios, by group. Pure data, one scenario per line.
// - A group is one fresh app launch; key order is the order the groups launch in.
// - Order inside a group is the state each scenario inherits from the one before: add a scenario after the one whose
//   state it needs, or at the end of `workspace` if it needs none.
// - A scenario is `desktop/smoke-<name>.cjs` exporting `run(ctx)`; it must not call Electron APIs at module load.
// oxfmt-ignore
module.exports = {
  chrome: [
    "shell",
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
