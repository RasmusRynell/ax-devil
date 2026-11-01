//! The one declaration of every setting. Defaults, parsing and validation are generated from it.

use crate::{env, kind::Kind};
use serde::Serialize;
use serde_json::Value;

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
    },
];

impl Setting {
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
