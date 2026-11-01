// Generated from crates/settings by make; do not edit.

/** Every setting, in the order the settings dialog shows them. */
export const SETTINGS = [
  {
    "key": "ui.theme",
    "kind": {
      "type": "choice",
      "options": [
        [
          "auto",
          "Auto"
        ],
        [
          "light",
          "Light"
        ],
        [
          "dark",
          "Dark"
        ]
      ],
      "default": "auto"
    },
    "label": "Theme",
    "page": "General",
    "section": "Appearance",
    "live": true,
    "env": false,
    "secret": false
  },
  {
    "key": "ui.quick_setup_done",
    "kind": {
      "type": "bool",
      "default": false
    },
    "label": "Quick Setup done",
    "page": null,
    "section": "",
    "live": true,
    "env": false,
    "secret": false
  },
  {
    "key": "playback.video_cache",
    "kind": {
      "type": "auto_or_whole",
      "min": 256,
      "max": 1048576
    },
    "label": "Memory for video caching (MiB)",
    "page": "Playback",
    "section": "Memory",
    "live": true,
    "env": false,
    "secret": false
  }
] as const;

/** Every command with its default key, in menu order. */
export const COMMANDS = [
  {
    "id": "new-workspace",
    "label": "New Workspace",
    "key": null,
    "repeats": false
  },
  {
    "id": "open-workspace",
    "label": "Open Workspace",
    "key": "Ctrl+O",
    "repeats": false
  },
  {
    "id": "save-workspace",
    "label": "Save Workspace",
    "key": "Ctrl+S",
    "repeats": false
  },
  {
    "id": "save-workspace-as",
    "label": "Save Workspace As",
    "key": "Ctrl+Shift+S",
    "repeats": false
  },
  {
    "id": "add-video",
    "label": "Add Video",
    "key": "Ctrl+N",
    "repeats": false
  },
  {
    "id": "add-live-stream",
    "label": "Add Live Stream",
    "key": "Ctrl+L",
    "repeats": false
  },
  {
    "id": "add-playlist",
    "label": "Add Playlist",
    "key": "Ctrl+Shift+N",
    "repeats": false
  },
  {
    "id": "keyboard-shortcuts",
    "label": "Keyboard Shortcuts",
    "key": "Ctrl+K",
    "repeats": false
  },
  {
    "id": "settings",
    "label": "Settings",
    "key": "Ctrl+,",
    "repeats": false
  },
  {
    "id": "exit",
    "label": "Exit",
    "key": "Ctrl+Q",
    "repeats": false
  },
  {
    "id": "fullscreen",
    "label": "Toggle Fullscreen",
    "key": "F11",
    "repeats": false
  },
  {
    "id": "lane-fullscreen",
    "label": "Toggle Lane Fullscreen",
    "key": "F",
    "repeats": false
  },
  {
    "id": "media-tools",
    "label": "Media Tools Panel",
    "key": "Ctrl+B",
    "repeats": false
  },
  {
    "id": "sidebar",
    "label": "Sidebar",
    "key": "Ctrl+\\",
    "repeats": false
  },
  {
    "id": "play-pause",
    "label": "Play / Pause",
    "key": "Space",
    "repeats": false
  },
  {
    "id": "step-forward",
    "label": "Step Forward 1 Frame",
    "key": ".",
    "repeats": true
  },
  {
    "id": "step-back",
    "label": "Step Back 1 Frame",
    "key": ",",
    "repeats": true
  },
  {
    "id": "step-forward-10",
    "label": "Step Forward 10 Frames",
    "key": "Right",
    "repeats": true
  },
  {
    "id": "step-back-10",
    "label": "Step Back 10 Frames",
    "key": "Left",
    "repeats": true
  },
  {
    "id": "speed-up",
    "label": "Playback Speed Up",
    "key": "]",
    "repeats": true
  },
  {
    "id": "speed-down",
    "label": "Playback Speed Down",
    "key": "[",
    "repeats": true
  },
  {
    "id": "lane-names",
    "label": "Show Lane Names",
    "key": null,
    "repeats": false
  },
  {
    "id": "object-details",
    "label": "Object Hover and Click Details",
    "key": null,
    "repeats": false
  },
  {
    "id": "info-overlay",
    "label": "Toggle Info Overlay",
    "key": "I",
    "repeats": false
  },
  {
    "id": "add-bookmark",
    "label": "Add Bookmark",
    "key": "M",
    "repeats": false
  },
  {
    "id": "export-video",
    "label": "Export Video",
    "key": "Ctrl+E",
    "repeats": false
  },
  {
    "id": "render-catalogs",
    "label": "Render Catalogs",
    "key": "Ctrl+R",
    "repeats": false
  },
  {
    "id": "debug-metrics",
    "label": "Debug Metrics",
    "key": "Ctrl+D",
    "repeats": false
  },
  {
    "id": "plugins",
    "label": "Plugins",
    "key": null,
    "repeats": false
  },
  {
    "id": "collect-metrics",
    "label": "Collect Debug Metrics",
    "key": null,
    "repeats": false
  },
  {
    "id": "quick-setup",
    "label": "Quick Setup",
    "key": null,
    "repeats": false
  },
  {
    "id": "about",
    "label": "About",
    "key": null,
    "repeats": false
  }
] as const;

/** Keys the desktop shell keeps for page zoom; no command may use them. */
export const RESERVED = [
  "Ctrl+=",
  "Ctrl++",
  "Ctrl+-",
  "Ctrl+0"
] as const;

/** The dotted key of a setting. */
export type SettingKey = (typeof SETTINGS)[number]["key"];

/** The id of a command. */
export type CommandId = (typeof COMMANDS)[number]["id"];
