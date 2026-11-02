//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use crate::{env, kind::Kind};
use serde::Serialize;
use serde_json::Value;

/// Shown in place of a literal secret; sending it back keeps the stored secret.
pub const MASK: &str = "********";

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

/// Every setting the app has.
pub const TABLE: &[Setting] = &[
    Setting {
        key: "ui.theme",
        kind: Kind::Choice {
            options: &[("auto", "Auto"), ("light", "Light"), ("dark", "Dark")],
            default: "auto",
        },
        label: "Theme",
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
        page: Some("Playback"),
        section: "Memory",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "storage.cache_dir",
        // Empty is the standard XDG cache folder. `--cache-dir` wins over the setting.
        kind: Kind::Folder,
        label: "Cache folder",
        page: Some("Storage"),
        section: "Folders",
        live: false,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
    Setting {
        key: "storage.catalog_dir",
        // Empty is the standard XDG data folder's `ax-devil/catalogs`.
        kind: Kind::Folder,
        label: "Render catalog folder",
        page: Some("Storage"),
        section: "Folders",
        live: false,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Server,
    },
];

impl Setting {
    /// Whether `text` is the secret itself rather than empty or a `$NAME` reference to it.
    fn literal(&self, text: &str) -> bool {
        !text.is_empty() && !(self.env && env::reference(text).is_some())
    }

    /// `value` as shown to a person: the literal in a secret row is masked, everything else as written.
    pub fn shown(&self, value: &Value) -> Value {
        match value.as_str() {
            Some(text) if self.secret && self.literal(text) => Value::String(MASK.into()),
            _ => value.clone(),
        }
    }

    /// Whether a person sent back the masked secret, which means "unchanged".
    pub(crate) fn is_masked(&self, value: &Value) -> bool {
        self.secret && value == MASK
    }

    /// Accepts `value` if it fits the kind. A reference in a setting that takes them is expanded by `lookup` first,
    /// so what is checked is what will be used. The reason never contains the value.
    pub(crate) fn check(
        &self,
        value: &Value,
        lookup: &dyn Fn(&str) -> Option<String>,
    ) -> Result<(), String> {
        if !self.env {
            return self.kind.validate(value);
        }
        let reference = value.as_str().and_then(env::reference);
        self.kind
            .validate(&env::resolve(value, lookup))
            .map_err(|reason| match reference {
                Some(name) => format!("{reason} (from ${name})"),
                None => reason,
            })
    }
}
