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
      "toggle": false,
      "default": "auto"
    },
    "label": "Theme",
    "description": null,
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
      "toggle": false,
      "default": "system"
    },
    "label": "Text size",
    "description": null,
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
    "description": null,
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
    "description": "Name the object under the pointer; click to select it and open its page.",
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
    "description": "Name each video lane; names hide while frame info is shown.",
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
    "description": null,
    "page": null,
    "section": "",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "personal"
  },
  {
    "key": "overlays.default_sync",
    "kind": {
      "type": "sync",
      "default": {
        "tolerance_ms": 50
      }
    },
    "label": "Default overlay sync",
    "description": null,
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
    "description": null,
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
    "description": null,
    "page": "Playback",
    "section": "Memory",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.protocol",
    "kind": {
      "type": "choice",
      "options": [
        [
          "https",
          "HTTPS"
        ],
        [
          "http",
          "HTTP"
        ]
      ],
      "toggle": true,
      "default": "https"
    },
    "label": "Camera protocol",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": true,
    "scope": "server"
  },
  {
    "key": "camera.host",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_TARGET_ADDR"
    },
    "label": "Host",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.user",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_TARGET_USER"
    },
    "label": "Username",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.password",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_TARGET_PASS"
    },
    "label": "Password",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": true,
    "secret": true,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.rtsp_port",
    "kind": {
      "type": "whole",
      "min": 1,
      "max": 65535,
      "default": 554
    },
    "label": "RTSP port",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.timeout",
    "kind": {
      "type": "whole",
      "min": 1,
      "max": 600,
      "default": 10
    },
    "label": "Connection timeout (seconds)",
    "description": null,
    "page": "Stream defaults",
    "section": "Device",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.head",
    "kind": {
      "type": "whole",
      "min": 1,
      "max": 255,
      "default": 1
    },
    "label": "Camera head",
    "description": null,
    "page": "Stream defaults",
    "section": "Video and overlay",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.resolution",
    "kind": {
      "type": "text",
      "default": ""
    },
    "label": "Resolution",
    "description": null,
    "page": null,
    "section": "",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.overlay",
    "kind": {
      "type": "choice",
      "options": [
        [
          "none",
          "None"
        ],
        [
          "rtsp",
          "RTSP Embedded"
        ],
        [
          "mqtt",
          "MQTT"
        ],
        [
          "datahub",
          "DataHub WebSocket"
        ]
      ],
      "toggle": false,
      "default": "datahub"
    },
    "label": "Overlay mode",
    "description": null,
    "page": "Stream defaults",
    "section": "Video and overlay",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "camera.rtsp_handler",
    "kind": {
      "type": "decoder",
      "default": "ONVIF_XML"
    },
    "label": "RTSP decoder",
    "description": null,
    "page": "Stream defaults",
    "section": "Video and overlay",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.host",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_MQTT_BROKER_ADDR"
    },
    "label": "Broker host",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.port",
    "kind": {
      "type": "whole",
      "min": 1,
      "max": 65535,
      "default": 1883
    },
    "label": "Broker port",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.user",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_MQTT_BROKER_USER"
    },
    "label": "Broker username",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.password",
    "kind": {
      "type": "text",
      "default": "$AX_DEVIL_MQTT_BROKER_PASS"
    },
    "label": "Broker password",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": true,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.source_key",
    "kind": {
      "type": "text",
      "default": "com.axis.scene.frame.v1#1"
    },
    "label": "Data source",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "broker.handler",
    "kind": {
      "type": "decoder",
      "default": "ADF_V1_FRAME"
    },
    "label": "Decoder",
    "description": null,
    "page": "Analytics",
    "section": "MQTT analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "datahub.topic",
    "kind": {
      "type": "text",
      "default": "com.axis.scene.frame.v1"
    },
    "label": "Topic",
    "description": null,
    "page": "Analytics",
    "section": "DataHub WebSocket analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "datahub.channel",
    "kind": {
      "type": "whole",
      "min": 1,
      "max": 4294967295,
      "default": 1
    },
    "label": "Topic channel",
    "description": null,
    "page": "Analytics",
    "section": "DataHub WebSocket analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "datahub.handler",
    "kind": {
      "type": "decoder",
      "default": "ADF_V1_FRAME"
    },
    "label": "Decoder",
    "description": null,
    "page": "Analytics",
    "section": "DataHub WebSocket analytics",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "scan.on_startup",
    "kind": {
      "type": "bool",
      "default": true
    },
    "label": "Scan for cameras on startup",
    "description": null,
    "page": "Network scan",
    "section": "Discovery",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "scan.sign_in",
    "kind": {
      "type": "bool",
      "default": true
    },
    "label": "Try to sign in to found cameras by default",
    "description": null,
    "page": "Network scan",
    "section": "Discovery",
    "live": true,
    "env": false,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "scan.range",
    "kind": {
      "type": "text",
      "default": "192.168.1.0/24"
    },
    "label": "Default IP range",
    "description": null,
    "page": "Network scan",
    "section": "Network scan",
    "live": true,
    "env": true,
    "secret": false,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "scan.logins",
    "kind": {
      "type": "records",
      "fields": [
        "user",
        "password"
      ],
      "default": [
        [
          "$AX_DEVIL_TARGET_USER",
          "$AX_DEVIL_TARGET_PASS"
        ]
      ]
    },
    "label": "Scan logins",
    "description": null,
    "page": "Network scan",
    "section": "Sign-in logins",
    "live": true,
    "env": true,
    "secret": true,
    "quick_setup": false,
    "scope": "server"
  },
  {
    "key": "storage.cache_dir",
    "kind": {
      "type": "folder"
    },
    "label": "Cache folder",
    "description": null,
    "page": "Storage",
    "section": "Folders",
    "live": false,
    "env": true,
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
    "description": null,
    "page": "Storage",
    "section": "Folders",
    "live": false,
    "env": true,
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
    "repeats": false,
    "description": null
  },
  {
    "id": "open-workspace",
    "label": "Open Workspace",
    "category": "Application",
    "key": "Ctrl+O",
    "repeats": false,
    "description": null
  },
  {
    "id": "save-workspace",
    "label": "Save Workspace",
    "category": "Application",
    "key": "Ctrl+S",
    "repeats": false,
    "description": null
  },
  {
    "id": "save-workspace-as",
    "label": "Save Workspace As",
    "category": "Application",
    "key": "Ctrl+Shift+S",
    "repeats": false,
    "description": null
  },
  {
    "id": "add-video",
    "label": "Add Video",
    "category": "Application",
    "key": "Ctrl+N",
    "repeats": false,
    "description": null
  },
  {
    "id": "add-live-stream",
    "label": "Add Live Stream",
    "category": "Application",
    "key": "Ctrl+L",
    "repeats": false,
    "description": null
  },
  {
    "id": "add-playlist",
    "label": "Add Playlist",
    "category": "Application",
    "key": "Ctrl+Shift+N",
    "repeats": false,
    "description": null
  },
  {
    "id": "keyboard-shortcuts",
    "label": "Keyboard Shortcuts",
    "category": "Application",
    "key": "Ctrl+K",
    "repeats": false,
    "description": null
  },
  {
    "id": "settings",
    "label": "Settings",
    "category": "Application",
    "key": "Ctrl+,",
    "repeats": false,
    "description": null
  },
  {
    "id": "exit",
    "label": "Exit",
    "category": "Application",
    "key": "Ctrl+Q",
    "repeats": false,
    "description": null
  },
  {
    "id": "fullscreen",
    "label": "Fullscreen",
    "category": "View",
    "key": "F11",
    "repeats": false,
    "description": null
  },
  {
    "id": "lane-fullscreen",
    "label": "Toggle Lane Fullscreen",
    "category": "View",
    "key": "F",
    "repeats": false,
    "description": null
  },
  {
    "id": "zoom-in",
    "label": "Zoom In",
    "category": "View",
    "key": "=",
    "repeats": true,
    "description": null
  },
  {
    "id": "zoom-out",
    "label": "Zoom Out",
    "category": "View",
    "key": "-",
    "repeats": true,
    "description": null
  },
  {
    "id": "zoom-reset",
    "label": "Reset Zoom",
    "category": "View",
    "key": "0",
    "repeats": false,
    "description": null
  },
  {
    "id": "media-tools",
    "label": "Toggle Media Tools Panel",
    "category": "View",
    "key": "Ctrl+B",
    "repeats": false,
    "description": null
  },
  {
    "id": "sidebar",
    "label": "Sidebar",
    "category": "View",
    "key": "Ctrl+\\",
    "repeats": false,
    "description": null
  },
  {
    "id": "play-pause",
    "label": "Play / Pause",
    "category": "Playback",
    "key": "Space",
    "repeats": false,
    "description": null
  },
  {
    "id": "step-forward",
    "label": "Step Forward 1 Frame",
    "category": "Playback",
    "key": ".",
    "repeats": true,
    "description": null
  },
  {
    "id": "step-back",
    "label": "Step Backward 1 Frame",
    "category": "Playback",
    "key": ",",
    "repeats": true,
    "description": null
  },
  {
    "id": "step-forward-10",
    "label": "Step Forward 10 Frames",
    "category": "Playback",
    "key": "Right",
    "repeats": true,
    "description": null
  },
  {
    "id": "step-back-10",
    "label": "Step Backward 10 Frames",
    "category": "Playback",
    "key": "Left",
    "repeats": true,
    "description": null
  },
  {
    "id": "previous-entry",
    "label": "Previous Playlist Entry",
    "category": "Navigation",
    "key": "Ctrl+Left",
    "repeats": false,
    "description": null
  },
  {
    "id": "next-entry",
    "label": "Next Playlist Entry",
    "category": "Navigation",
    "key": "Ctrl+Right",
    "repeats": false,
    "description": null
  },
  {
    "id": "speed-up",
    "label": "Playback Speed Up",
    "category": "Playback",
    "key": "]",
    "repeats": true,
    "description": null
  },
  {
    "id": "speed-down",
    "label": "Playback Speed Down",
    "category": "Playback",
    "key": "[",
    "repeats": true,
    "description": null
  },
  {
    "id": "lane-names",
    "label": "Show Lane Names",
    "category": "View",
    "key": null,
    "repeats": false,
    "description": "Name each video lane; names hide while frame info is shown."
  },
  {
    "id": "object-details",
    "label": "Object Hover and Click Details",
    "category": "View",
    "key": null,
    "repeats": false,
    "description": "Name the object under the pointer; click to select it and open its page."
  },
  {
    "id": "info-overlay",
    "label": "Toggle Info Overlay",
    "category": "View",
    "key": "I",
    "repeats": false,
    "description": null
  },
  {
    "id": "add-bookmark",
    "label": "Add Bookmark",
    "category": "View",
    "key": "M",
    "repeats": false,
    "description": null
  },
  {
    "id": "export-video",
    "label": "Export Video",
    "category": "View",
    "key": "Ctrl+E",
    "repeats": false,
    "description": null
  },
  {
    "id": "debug-metrics",
    "label": "Debug Metrics",
    "category": "Application",
    "key": "Ctrl+D",
    "repeats": false,
    "description": null
  },
  {
    "id": "plugins",
    "label": "Plugins",
    "category": "Application",
    "key": null,
    "repeats": false,
    "description": null
  },
  {
    "id": "collect-metrics",
    "label": "Collect Debug Metrics",
    "category": "Application",
    "key": null,
    "repeats": false,
    "description": null
  },
  {
    "id": "quick-setup",
    "label": "Quick Setup",
    "category": "Application",
    "key": null,
    "repeats": false,
    "description": null
  },
  {
    "id": "about",
    "label": "About",
    "category": "Application",
    "key": null,
    "repeats": false,
    "description": null
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
