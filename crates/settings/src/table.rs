//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use crate::kind::{Kind, NoFilter, StickyValue, SyncValue};
use serde::Serialize;

/// Who a setting belongs to, which decides who may read and change it on a backend that several people use.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    /// How one person's app behaves, such as the shortcuts and the defaults for new overlays.
    Personal,
    /// What the server works with: device and broker defaults, folders, cache.
    Server,
    /// What depends on the screen in front of you, such as the theme and the text size. It lives on that device and
    /// never reaches the server.
    Device,
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
    /// Whether the value is a credential: write-only, a client learns whether it is set, never the literal.
    pub secret: bool,
    /// Whether only a caller with Full access may change it: it reaches outside ax-devil.
    pub full_access: bool,
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

/// What an entry in [`TABLE`] gets for the fields it does not state. Only the common values are here: every entry
/// states its own `key`, `kind`, `label`, `page`, `section` and `scope`. The placeholders for those are never read;
/// `scope` is the stricter one, so an entry that forgot it would not open a setting to every person.
const BASE: Setting = Setting {
    key: "",
    kind: Kind::Bool { default: false },
    label: "",
    description: None,
    page: None,
    section: "",
    live: true,
    env: true,
    secret: false,
    full_access: false,
    quick_setup: false,
    scope: Scope::Server,
};

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
        page: Some("General"),
        section: "Appearance",
        env: false,
        quick_setup: true,
        scope: Scope::Device,
        ..BASE
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
        page: Some("General"),
        section: "Appearance",
        env: false,
        quick_setup: true,
        scope: Scope::Device,
        ..BASE
    },
    Setting {
        key: "ui.quick_setup_done",
        kind: Kind::Bool { default: false },
        label: "Quick Setup done",
        page: None,
        section: "",
        env: false,
        scope: Scope::Personal,
        ..BASE
    },
    Setting {
        key: "overlays.object_details",
        kind: Kind::Bool { default: true },
        label: "Show object details on hover and click",
        description: Some(OBJECT_DETAILS_DESCRIPTION),
        page: Some("General"),
        section: "Video overlays",
        env: false,
        scope: Scope::Personal,
        ..BASE
    },
    Setting {
        key: "overlays.lane_names",
        kind: Kind::Bool { default: true },
        label: "Show lane names",
        description: Some(LANE_NAMES_DESCRIPTION),
        page: Some("General"),
        section: "Video overlays",
        env: false,
        scope: Scope::Personal,
        ..BASE
    },
    Setting {
        key: "overlays.default_catalog",
        // A catalog id such as `built-in:standard`; empty is the built-in default. Chosen from a lane's menu.
        kind: Kind::Text { default: "" },
        label: "Default catalog",
        page: None,
        section: "",
        env: false,
        scope: Scope::Personal,
        ..BASE
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
        page: None,
        section: "",
        env: false,
        scope: Scope::Personal,
        ..BASE
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
        page: None,
        section: "",
        env: false,
        scope: Scope::Personal,
        ..BASE
    },
    Setting {
        key: "overlays.default_object_filter",
        // What a new viewer or lane starts with; set from a lane's menu.
        kind: Kind::ObjectFilter { default: NoFilter },
        label: "Default object filter",
        page: None,
        section: "",
        env: false,
        scope: Scope::Personal,
        ..BASE
    },
    Setting {
        key: "playback.video_cache",
        // Auto, or MiB shared by every open video. The upper bound is 1 TiB.
        kind: Kind::AutoOrWhole {
            min: 256,
            max: 1024 * 1024,
        },
        label: "Video cache",
        page: Some("Playback"),
        section: "",
        env: false,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "playback.prepare_next_video",
        // Read by every open playlist stream as it ticks, so a change applies at once.
        kind: Kind::Bool { default: false },
        label: "Prepare next video",
        page: Some("Playback"),
        section: "",
        env: false,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.protocol",
        kind: Kind::Choice {
            options: &[("https", "HTTPS"), ("http", "HTTP")],
            toggle: true,
            default: "https",
        },
        label: "Camera protocol",
        page: Some("Stream defaults"),
        section: "Device",
        env: false,
        quick_setup: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.host",
        // What a new Live Stream starts from; a reference stays one in the item.
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_ADDR",
        },
        label: "Host",
        page: Some("Stream defaults"),
        section: "Device",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.user",
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_USER",
        },
        label: "Username",
        page: Some("Stream defaults"),
        section: "Device",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.password",
        kind: Kind::Text {
            default: "$AX_DEVIL_TARGET_PASS",
        },
        label: "Password",
        page: Some("Stream defaults"),
        section: "Device",
        secret: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.rtsp_port",
        kind: Kind::Whole {
            min: 1,
            max: 65535,
            default: 554,
        },
        label: "RTSP port",
        page: Some("Stream defaults"),
        section: "Device",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.rtsp_route",
        kind: Kind::Choice {
            options: &[("direct", "Direct"), ("web_api", "Via web API")],
            toggle: true,
            default: "direct",
        },
        label: "RTSP route",
        page: Some("Stream defaults"),
        section: "Device",
        env: false,
        scope: Scope::Server,
        ..BASE
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
        page: Some("Stream defaults"),
        section: "Device",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.head",
        kind: Kind::Whole {
            min: 1,
            max: 9999,
            default: 1,
        },
        label: "Camera head",
        page: Some("Stream defaults"),
        section: "Video and overlay",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.resolution",
        // Empty is the camera's own resolution.
        kind: Kind::Text { default: "" },
        label: "Resolution",
        page: None,
        section: "",
        env: false,
        scope: Scope::Server,
        ..BASE
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
        label: "Overlay mode",
        page: Some("Stream defaults"),
        section: "Video and overlay",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "camera.rtsp_handler",
        kind: Kind::Decoder {
            default: "ONVIF_XML",
        },
        label: "RTSP decoder",
        page: Some("Stream defaults"),
        section: "Video and overlay",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.host",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_ADDR",
        },
        label: "Broker host",
        page: Some("Analytics"),
        section: "MQTT analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.port",
        kind: Kind::Whole {
            min: 1,
            max: 65535,
            default: 1883,
        },
        label: "Broker port",
        page: Some("Analytics"),
        section: "MQTT analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.user",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_USER",
        },
        label: "Broker username",
        page: Some("Analytics"),
        section: "MQTT analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.password",
        kind: Kind::Text {
            default: "$AX_DEVIL_MQTT_BROKER_PASS",
        },
        label: "Broker password",
        page: Some("Analytics"),
        section: "MQTT analytics",
        secret: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.source_key",
        kind: Kind::Text {
            default: "com.axis.scene.frame.v1#1",
        },
        label: "Data source",
        page: Some("Analytics"),
        section: "MQTT analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "broker.handler",
        kind: Kind::Decoder {
            default: "ADF_V1_FRAME",
        },
        label: "Decoder",
        page: Some("Analytics"),
        section: "MQTT analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "datahub.topic",
        kind: Kind::Text {
            default: "com.axis.scene.frame.v1",
        },
        label: "Topic",
        page: Some("Analytics"),
        section: "DataHub WebSocket analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "datahub.channel",
        kind: Kind::Whole {
            min: 1,
            max: 4_294_967_295,
            default: 1,
        },
        label: "Topic channel",
        page: Some("Analytics"),
        section: "DataHub WebSocket analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "datahub.handler",
        kind: Kind::Decoder {
            default: "ADF_V1_FRAME",
        },
        label: "Decoder",
        page: Some("Analytics"),
        section: "DataHub WebSocket analytics",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "scan.on_startup",
        kind: Kind::Bool { default: true },
        label: "Scan for cameras on startup",
        page: Some("Network scan"),
        section: "Discovery",
        env: false,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "scan.sign_in",
        // Whether a scan signs in to a device that answered as an Axis camera, the scan at launch included.
        kind: Kind::Bool { default: true },
        label: "Try to sign in to found cameras by default",
        page: Some("Network scan"),
        section: "Discovery",
        env: false,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "scan.range",
        kind: Kind::Text {
            default: "192.168.1.0/24",
        },
        label: "Default IP range",
        page: Some("Network scan"),
        section: "Network scan",
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "scan.logins",
        // Tried in order on a device that answered as an Axis camera; each field may be a `$NAME` reference.
        kind: Kind::Records {
            fields: &["user", "password"],
            default: &[&["$AX_DEVIL_TARGET_USER", "$AX_DEVIL_TARGET_PASS"]],
        },
        label: "Scan logins",
        page: Some("Network scan"),
        section: "Sign-in logins",
        secret: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "storage.cache_dir",
        // Empty is the standard XDG cache folder. `--cache-dir` wins over the setting.
        kind: Kind::Folder,
        label: "Cache folder",
        page: Some("Storage"),
        section: "Folders",
        live: false,
        full_access: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "storage.catalog_dir",
        // Empty is the standard XDG data folder's `ax-devil/catalogs`.
        kind: Kind::Folder,
        label: "Render catalog folder",
        page: Some("Storage"),
        section: "Folders",
        live: false,
        full_access: true,
        scope: Scope::Server,
        ..BASE
    },
    Setting {
        key: "server.password_full_access",
        // Read on every request and socket hello. A Session keeps the Full access it was created with.
        kind: Kind::Bool { default: false },
        label: "Full access with password",
        description: Some(
            "Anyone with the server password can install plugins and read this machine's environment.",
        ),
        page: Some("Storage"),
        section: "Folders",
        env: false,
        full_access: true,
        scope: Scope::Server,
        ..BASE
    },
];
