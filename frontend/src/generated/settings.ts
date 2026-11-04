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
          "System"
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
    "secret": false,
    "quick_setup": true,
    "scope": "personal"
  },
  {
    "key": "ui.text_size",
    "kind": {
      "type": "choice",
      "options": [
        [
          "system",
          "System"
        ],
        [
          "small",
          "Small"
        ],
        [
          "medium",
          "Medium"
        ],
        [
          "large",
          "Large"
        ],
        [
          "larger",
          "Larger"
        ]
      ],
      "default": "system"
    },
    "label": "Text size",
    "page": "General",
    "section": "Appearance",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": true,
    "scope": "personal"
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
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
  },
  {
    "key": "overlays.object_details",
    "kind": {
      "type": "bool",
      "default": true
    },
    "label": "Show object details on hover and click",
    "page": "General",
    "section": "Video overlays",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
  },
  {
    "key": "overlays.lane_names",
    "kind": {
      "type": "bool",
      "default": true
    },
    "label": "Show lane names",
    "page": "General",
    "section": "Video overlays",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
  },
  {
    "key": "overlays.default_catalog",
    "kind": {
      "type": "text",
      "default": ""
    },
    "label": "Default catalog",
    "page": null,
    "section": "",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
  },
  {
    "key": "overlays.default_sticky",
    "kind": {
      "type": "sticky",
      "default": {
        "enabled": true,
        "timeout_ms": 2050,
        "opacity": 1.0
      }
    },
    "label": "Default sticky overlays",
    "page": null,
    "section": "",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
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
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "storage.cache_dir",
    "kind": {
      "type": "folder"
    },
    "label": "Cache folder",
    "page": "Storage",
    "section": "Folders",
    "live": false,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "storage.catalog_dir",
    "kind": {
      "type": "folder"
    },
    "label": "Render catalog folder",
    "page": "Storage",
    "section": "Folders",
    "live": false,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  }
] as const;

/** Every command with its default key, in menu order. */
export const COMMANDS = [
  {
    "id": "new-workspace",
    "label": "New Workspace",
    "category": "Application",
    "key": null,
    "repeats": false
  },
  {
    "id": "open-workspace",
    "label": "Open Workspace",
    "category": "Application",
    "key": "Ctrl+O",
    "repeats": false
  },
  {
    "id": "save-workspace",
    "label": "Save Workspace",
    "category": "Application",
    "key": "Ctrl+S",
    "repeats": false
  },
  {
    "id": "save-workspace-as",
    "label": "Save Workspace As",
    "category": "Application",
    "key": "Ctrl+Shift+S",
    "repeats": false
  },
  {
    "id": "add-video",
    "label": "Add Video",
    "category": "Application",
    "key": "Ctrl+N",
    "repeats": false
  },
  {
    "id": "add-live-stream",
    "label": "Add Live Stream",
    "category": "Application",
    "key": "Ctrl+L",
    "repeats": false
  },
  {
    "id": "add-playlist",
    "label": "Add Playlist",
    "category": "Application",
    "key": "Ctrl+Shift+N",
    "repeats": false
  },
  {
    "id": "keyboard-shortcuts",
    "label": "Keyboard Shortcuts",
    "category": "Application",
    "key": "Ctrl+K",
    "repeats": false
  },
  {
    "id": "settings",
    "label": "Settings",
    "category": "Application",
    "key": "Ctrl+,",
    "repeats": false
  },
  {
    "id": "exit",
    "label": "Exit",
    "category": "Application",
    "key": "Ctrl+Q",
    "repeats": false
  },
  {
    "id": "fullscreen",
    "label": "Fullscreen",
    "category": "View",
    "key": "F11",
    "repeats": false
  },
  {
    "id": "lane-fullscreen",
    "label": "Toggle Lane Fullscreen",
    "category": "View",
    "key": "F",
    "repeats": false
  },
  {
    "id": "zoom-in",
    "label": "Zoom In",
    "category": "View",
    "key": "=",
    "repeats": true
  },
  {
    "id": "zoom-out",
    "label": "Zoom Out",
    "category": "View",
    "key": "-",
    "repeats": true
  },
  {
    "id": "zoom-reset",
    "label": "Reset Zoom",
    "category": "View",
    "key": "0",
    "repeats": false
  },
  {
    "id": "media-tools",
    "label": "Toggle Media Tools Panel",
    "category": "View",
    "key": "Ctrl+B",
    "repeats": false
  },
  {
    "id": "sidebar",
    "label": "Sidebar",
    "category": "View",
    "key": "Ctrl+\\",
    "repeats": false
  },
  {
    "id": "play-pause",
    "label": "Play / Pause",
    "category": "Playback",
    "key": "Space",
    "repeats": false
  },
  {
    "id": "step-forward",
    "label": "Step Forward 1 Frame",
    "category": "Playback",
    "key": ".",
    "repeats": true
  },
  {
    "id": "step-back",
    "label": "Step Backward 1 Frame",
    "category": "Playback",
    "key": ",",
    "repeats": true
  },
  {
    "id": "step-forward-10",
    "label": "Step Forward 10 Frames",
    "category": "Playback",
    "key": "Right",
    "repeats": true
  },
  {
    "id": "step-back-10",
    "label": "Step Backward 10 Frames",
    "category": "Playback",
    "key": "Left",
    "repeats": true
  },
  {
    "id": "previous-entry",
    "label": "Previous Playlist Entry",
    "category": "Navigation",
    "key": "Ctrl+Left",
    "repeats": false
  },
  {
    "id": "next-entry",
    "label": "Next Playlist Entry",
    "category": "Navigation",
    "key": "Ctrl+Right",
    "repeats": false
  },
  {
    "id": "speed-up",
    "label": "Playback Speed Up",
    "category": "Playback",
    "key": "]",
    "repeats": true
  },
  {
    "id": "speed-down",
    "label": "Playback Speed Down",
    "category": "Playback",
    "key": "[",
    "repeats": true
  },
  {
    "id": "lane-names",
    "label": "Show Lane Names",
    "category": "View",
    "key": null,
    "repeats": false
  },
  {
    "id": "object-details",
    "label": "Object Hover and Click Details",
    "category": "View",
    "key": null,
    "repeats": false
  },
  {
    "id": "info-overlay",
    "label": "Toggle Info Overlay",
    "category": "View",
    "key": "I",
    "repeats": false
  },
  {
    "id": "add-bookmark",
    "label": "Add Bookmark",
    "category": "View",
    "key": "M",
    "repeats": false
  },
  {
    "id": "export-video",
    "label": "Export Video",
    "category": "View",
    "key": "Ctrl+E",
    "repeats": false
  },
  {
    "id": "render-catalogs",
    "label": "Render Catalogs",
    "category": "View",
    "key": "Ctrl+R",
    "repeats": false
  },
  {
    "id": "debug-metrics",
    "label": "Debug Metrics",
    "category": "Application",
    "key": "Ctrl+D",
    "repeats": false
  },
  {
    "id": "plugins",
    "label": "Plugins",
    "category": "Application",
    "key": null,
    "repeats": false
  },
  {
    "id": "collect-metrics",
    "label": "Collect Debug Metrics",
    "category": "Application",
    "key": null,
    "repeats": false
  },
  {
    "id": "quick-setup",
    "label": "Quick Setup",
    "category": "Application",
    "key": null,
    "repeats": false
  },
  {
    "id": "about",
    "label": "About",
    "category": "Application",
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
