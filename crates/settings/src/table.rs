//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use crate::kind::{Kind, StickyValue, SyncValue};
use serde::Serialize;

/// Who a setting belongs to, which decides who may read and change it on a backend that several people use.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    /// How one person's app behaves, such as the theme and the shortcuts.
    Personal,
    /// What the server works with: device and broker defaults, folders, cache.
    Server,
}

impl Scope {
    /// Every scope, for the person who owns the backend.
    pub const ALL: [Self; 2] = [Self::Personal, Self::Server];
}

/// One declared setting.
#[derive(Debug, Serialize)]
pub struct Setting {
    /// Dotted key in the config file, such as `ui.theme`.
    pub key: &'static str,
    /// What the setting holds.
    pub kind: Kind,
    /// The name the settings dialog shows.
    pub label: &'static str,
    /// What the setting does, shown as its tooltip; `None` shows the key.
    pub description: Option<&'static str>,
    /// The settings dialog page; `None` keeps the setting out of the dialog.
    pub page: Option<&'static str>,
    /// The heading on the page.
    pub section: &'static str,
    /// Whether a change applies without a restart.
    pub live: bool,
    /// Whether the value may be a `$NAME` reference to an environment variable.
    pub env: bool,
    /// Whether the value is a credential: the dialog masks a literal.
    pub secret: bool,
    /// Whether Quick Setup asks for it.
    pub quick_setup: bool,
    /// Who the setting belongs to.
    pub scope: Scope,
}

/// What the object-details overlay does: the setting's tooltip and its command's.
pub const OBJECT_DETAILS_DESCRIPTION: &str =
    "Name the object under the pointer; click to select it and open its page.";
/// What the lane-names overlay does: the setting's tooltip and its command's.
pub const LANE_NAMES_DESCRIPTION: &str =
    "Name each video lane; names hide while frame info is shown.";

/// Every setting the app has.
pub const TABLE: &[Setting] = &[
    Setting {
        key: "ui.theme",
        kind: Kind::Choice {
            options: &[("auto", "System"), ("light", "Light"), ("dark", "Dark")],
            toggle: false,
            default: "auto",
        },
        label: "Theme",
        description: None,
        page: Some("General"),
        section: "Appearance",
        live: true,
        env: false,
        secret: false,
        quick_setup: true,
        scope: Scope::Personal,
    },
    Setting {
        key: "ui.text_size",
        kind: Kind::Choice {
            options: &[
                ("system", "System"),
                ("small", "Small"),
                ("medium", "Medium"),
                ("large", "Large"),
                ("larger", "Larger"),
            ],
            toggle: false,
            default: "system",
        },
        label: "Text size",
        description: None,
        page: Some("General"),
        section: "Appearance",
        live: true,
        env: false,
        secret: false,
        quick_setup: true,
        scope: Scope::Personal,
    },
    Setting {
        key: "ui.quick_setup_done",
        kind: Kind::Bool { default: false },
        label: "Quick Setup done",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "overlays.object_details",
        kind: Kind::Bool { default: true },
        label: "Show object details on hover and click",
        description: Some(OBJECT_DETAILS_DESCRIPTION),
        page: Some("General"),
        section: "Video overlays",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "overlays.lane_names",
        kind: Kind::Bool { default: true },
        label: "Show lane names",
        description: Some(LANE_NAMES_DESCRIPTION),
        page: Some("General"),
        section: "Video overlays",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "overlays.default_catalog",
        // A catalog id such as `built-in:standard`; empty is the built-in default. Chosen from a lane's menu.
        kind: Kind::Text { default: "" },
        label: "Default catalog",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "overlays.default_sync",
        // What a new viewer or lane starts with; set from a lane's menu.
        kind: Kind::Sync {
            default: SyncValue {
                tolerance_ms: Some(50),
            },
        },
        label: "Default overlay sync",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "overlays.default_sticky",
        // What a new viewer or lane starts with; set from a lane's menu.
        kind: Kind::Sticky {
            default: StickyValue {
                enabled: true,
                timeout_ms: Some(2050),
                opacity: 1.0,
            },
        },
        label: "Default sticky overlays",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "playback.video_cache",
        // Auto, or MiB shared by every open video. The upper bound is 1 TiB.
        kind: Kind::AutoOrWhole {
            min: 256,
            max: 1024 * 1024,
        },
        label: "Memory for video caching (MiB)",
        description: None,
        page: Some("Playback"),
        section: "Memory",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.host",
        // What a new Live Stream starts from; a reference stays one in the item.
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_ADDR",
        },
        label: "Camera host",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.user",
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_USER",
        },
        label: "Camera username",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.password",
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_PASS",
        },
        label: "Camera password",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: true,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.protocol",
        kind: Kind::Choice {
            options: &[("https", "HTTPS"), ("http", "HTTP")],
            toggle: true,
            default: "https",
        },
        label: "Camera protocol",
        description: None,
        page: Some("Stream defaults"),
        section: "Device",
        live: true,
        env: false,
        secret: false,
        quick_setup: true,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.head",
        // No `$NAME` reference yet: frontend/src/shell/liveForm.ts:116 prefills the Add Live Stream form with the
        // raw value, which problemOf (liveForm.ts:212) refuses unless it is digits.
        kind: Kind::Whole {
            min: 1,
            max: 255,
            default: 1,
        },
        label: "Camera head",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.rtsp_port",
        kind: Kind::Whole {
            min: 1,
            max: 65535,
            default: 554,
        },
        label: "RTSP port",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.resolution",
        // Empty is the camera's own resolution.
        kind: Kind::Text { default: "" },
        label: "Resolution",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.overlay",
        kind: Kind::Choice {
            options: &[
                ("none", "None"),
                ("rtsp", "RTSP Embedded"),
                ("mqtt", "MQTT"),
                ("datahub", "DataHub WebSocket"),
            ],
            toggle: false,
            default: "datahub",
        },
        label: "Overlay",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.rtsp_handler",
        kind: Kind::Text {
            default: "ONVIF_XML",
        },
        label: "RTSP overlay format",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.timeout",
        // Seconds one request or connection attempt may take; read when connecting, never stored on an item.
        kind: Kind::Whole {
            min: 1,
            max: 600,
            default: 10,
        },
        label: "Connection timeout (seconds)",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "camera.confirmed",
        // The `$NAME` references the user let a Live Stream send to a host: reference name and resolved host.
        kind: Kind::Records {
            fields: &["reference", "host"],
            default: &[],
        },
        label: "Confirmed references",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "broker.host",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_ADDR",
        },
        label: "MQTT host",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "broker.user",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_USER",
        },
        label: "MQTT username",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "broker.password",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_PASS",
        },
        label: "MQTT password",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: true,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "broker.port",
        // No `$NAME` reference yet: frontend/src/shell/liveForm.ts:125 prefills the Add Live Stream form with the
        // raw value, which problemOf (liveForm.ts:212) refuses unless it is digits.
        kind: Kind::Whole {
            min: 1,
            max: 65535,
            default: 1883,
        },
        label: "MQTT port",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "broker.source_key",
        kind: Kind::Text {
            default: "com.axis.scene.frame.v1#1",
        },
        label: "MQTT data source",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "broker.handler",
        kind: Kind::Text {
            default: "ADF_V1_FRAME",
        },
        label: "MQTT overlay format",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "datahub.topic",
        kind: Kind::Text {
            default: "com.axis.scene.frame.v1",
        },
        label: "DataHub topic",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "datahub.channel",
        // No `$NAME` reference yet: frontend/src/shell/liveForm.ts:128 prefills the Add Live Stream form with the
        // raw value, which problemOf (liveForm.ts:212) refuses unless it is digits.
        kind: Kind::Whole {
            min: 1,
            max: 4_294_967_295,
            default: 1,
        },
        label: "DataHub channel",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "datahub.handler",
        kind: Kind::Text {
            default: "ADF_V1_FRAME",
        },
        label: "DataHub overlay format",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "scan.on_startup",
        kind: Kind::Bool { default: true },
        label: "Scan on startup",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "scan.sign_in",
        // Whether a scan the user starts signs in to a device that answered as an Axis camera; the scan at launch
        // never does.
        kind: Kind::Bool { default: true },
        label: "Sign in to found cameras",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "scan.range",
        kind: Kind::Text {
            default: "192.168.1.0/24",
        },
        label: "Scan range",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "scan.logins",
        // Tried in order on a device that answered as an Axis camera; each field may be a `$NAME` reference.
        kind: Kind::Records {
            fields: &["user", "password"],
            default: &[&["$AX_DEVIL_TARGET_USER", "$AX_DEVIL_TARGET_PASS"]],
        },
        label: "Scan logins",
        description: None,
        page: Some("Network scan"),
        section: "Sign-in logins",
        live: true,
        env: true,
        secret: true,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "storage.cache_dir",
        // Empty is the standard XDG cache folder. `--cache-dir` wins over the setting.
        kind: Kind::Folder,
        label: "Cache folder",
        description: None,
        page: Some("Storage"),
        section: "Folders",
        live: false,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "storage.catalog_dir",
        // Empty is the standard XDG data folder's `ax-devil/catalogs`.
        kind: Kind::Folder,
        label: "Render catalog folder",
        description: None,
        page: Some("Storage"),
        section: "Folders",
        live: false,
        env: true,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
];
