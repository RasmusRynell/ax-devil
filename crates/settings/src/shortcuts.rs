//! The keyboard commands, their default keys, and the rules for personal key overrides.
//!
//! Pure data and functions: the file I/O and the live state live in the crate root. A key is written
//! `Ctrl+Alt+Shift+<key>` (modifiers in that order, omitted when unused); the frontend matches events against the
//! same grammar. `<key>` is a letter (upper case), a digit, one ASCII symbol, `F1`..`F24`, or one of [`NAMED_KEYS`].

use std::collections::BTreeMap;

use crate::Error;
use serde::Serialize;

/// One command the user can run from the menus or the keyboard.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Command {
    /// Stable id, used in the settings file.
    pub id: &'static str,
    /// Menu and dialog label.
    pub label: &'static str,
    /// The default key in canonical form, or `None` when the command ships unbound.
    pub key: Option<&'static str>,
    /// Whether holding the key repeats the command.
    pub repeats: bool,
}

const fn command(
    id: &'static str,
    label: &'static str,
    key: Option<&'static str>,
    repeats: bool,
) -> Command {
    Command {
        id,
        label,
        key,
        repeats,
    }
}

/// Every command, in menu order.
pub const COMMANDS: &[Command] = &[
    command("new-workspace", "New Workspace", None, false),
    command("open-workspace", "Open Workspace", Some("Ctrl+O"), false),
    command("save-workspace", "Save Workspace", Some("Ctrl+S"), false),
    command(
        "save-workspace-as",
        "Save Workspace As",
        Some("Ctrl+Shift+S"),
        false,
    ),
    command("add-video", "Add Video", Some("Ctrl+N"), false),
    command("add-live-stream", "Add Live Stream", Some("Ctrl+L"), false),
    command("add-playlist", "Add Playlist", Some("Ctrl+Shift+N"), false),
    command(
        "keyboard-shortcuts",
        "Keyboard Shortcuts",
        Some("Ctrl+K"),
        false,
    ),
    command("settings", "Settings", Some("Ctrl+,"), false),
    command("exit", "Exit", Some("Ctrl+Q"), false),
    command("fullscreen", "Toggle Fullscreen", Some("F11"), false),
    command(
        "lane-fullscreen",
        "Toggle Lane Fullscreen",
        Some("F"),
        false,
    ),
    command("media-tools", "Media Tools Panel", Some("Ctrl+B"), false),
    command("sidebar", "Sidebar", Some("Ctrl+\\"), false),
    command("play-pause", "Play / Pause", Some("Space"), false),
    command("step-forward", "Step Forward 1 Frame", Some("."), true),
    command("step-back", "Step Back 1 Frame", Some(","), true),
    command(
        "step-forward-10",
        "Step Forward 10 Frames",
        Some("Right"),
        true,
    ),
    command("step-back-10", "Step Back 10 Frames", Some("Left"), true),
    command("speed-up", "Playback Speed Up", Some("]"), true),
    command("speed-down", "Playback Speed Down", Some("["), true),
    command("lane-names", "Show Lane Names", None, false),
    command(
        "object-details",
        "Object Hover and Click Details",
        None,
        false,
    ),
    command("info-overlay", "Toggle Info Overlay", Some("I"), false),
    command("add-bookmark", "Add Bookmark", Some("M"), false),
    command("export-video", "Export Video", Some("Ctrl+E"), false),
    command("render-catalogs", "Render Catalogs", Some("Ctrl+R"), false),
    command("debug-metrics", "Debug Metrics", Some("Ctrl+D"), false),
    command("plugins", "Plugins", None, false),
    command("collect-metrics", "Collect Debug Metrics", None, false),
    command("quick-setup", "Quick Setup", None, false),
    command("about", "About", None, false),
];

/// Keys the desktop shell keeps for page zoom (desktop/main.cjs); no command may use them.
pub const RESERVED: &[&str] = &["Ctrl+=", "Ctrl++", "Ctrl+-", "Ctrl+0"];

/// Non-character keys, spelled as the browser's `event.key` capitalised the way the shortcut writes them.
const NAMED_KEYS: &[&str] = &[
    "Space",
    "Left",
    "Right",
    "Up",
    "Down",
    "Enter",
    "Escape",
    "Tab",
    "Backspace",
    "Delete",
    "Insert",
    "Home",
    "End",
    "PageUp",
    "PageDown",
];

/// The canonical spelling of `key`, or why it is not a valid shortcut.
///
/// Modifiers are `Ctrl`, `Alt`, `Shift` (any case, any order, each at most once) and come out in that order. Shift
/// is refused with a symbol key, because the browser reports the shifted character instead of the key.
pub fn canonical(key: &str) -> Result<String, String> {
    let (modifiers, name) = if key == "+" {
        (None, "+")
    } else if let Some(rest) = key.strip_suffix("++") {
        (Some(rest), "+")
    } else {
        match key.rsplit_once('+') {
            Some((rest, name)) => (Some(rest), name),
            None => (None, key),
        }
    };
    let mut held = [false; 3];
    for part in modifiers.map_or_else(Vec::new, |rest| rest.split('+').collect()) {
        let slot = ["ctrl", "alt", "shift"]
            .iter()
            .position(|modifier| part.eq_ignore_ascii_case(modifier))
            .ok_or_else(|| format!("\"{part}\" is not a modifier; use Ctrl, Alt or Shift"))?;
        if std::mem::replace(&mut held[slot], true) {
            return Err(format!("{part} is given twice"));
        }
    }
    let name = canonical_name(name)?;
    let symbol = name.len() == 1 && !name.as_bytes()[0].is_ascii_alphanumeric();
    if symbol && held[2] {
        return Err(format!("Shift cannot be combined with {name}"));
    }
    let mut out = String::new();
    for (held, modifier) in held.iter().zip(["Ctrl", "Alt", "Shift"]) {
        if *held {
            out.push_str(modifier);
            out.push('+');
        }
    }
    out.push_str(&name);
    Ok(out)
}

fn canonical_name(name: &str) -> Result<String, String> {
    if let Some(named) = NAMED_KEYS
        .iter()
        .find(|named| named.eq_ignore_ascii_case(name))
    {
        return Ok((*named).to_owned());
    }
    let mut chars = name.chars();
    match (chars.next(), chars.next()) {
        (Some(only), None) if only.is_ascii_graphic() => Ok(only.to_ascii_uppercase().to_string()),
        (Some('F' | 'f'), Some(_)) => match name[1..].parse::<u8>() {
            Ok(number @ 1..=24) if !name[1..].starts_with('0') => Ok(format!("F{number}")),
            _ => Err(format!("\"{name}\" is not a key")),
        },
        (None, _) => Err("a shortcut needs a key".to_owned()),
        _ => Err(format!("\"{name}\" is not a key")),
    }
}

/// The key each command is bound to once `overrides` (id to key, `None` = unbound) are applied.
///
/// An override wins; a default whose key an override uses becomes unbound, so a key the user chose is never shared
/// with a default added in a later release. Overrides for unknown ids change nothing.
pub fn effective(
    overrides: &BTreeMap<String, Option<String>>,
) -> BTreeMap<&'static str, Option<String>> {
    let taken: Vec<String> = COMMANDS
        .iter()
        .filter_map(|command| canonical(overrides.get(command.id)?.as_deref()?).ok())
        .collect();
    COMMANDS
        .iter()
        .map(|command| {
            let key = match overrides.get(command.id) {
                Some(key) => key.clone(),
                None => command
                    .key
                    .filter(|key| !taken.iter().any(|taken| taken == key))
                    .map(str::to_owned),
            };
            (command.id, key)
        })
        .collect()
}

/// The overrides that produce `effective`: only the commands whose key differs from the default, in canonical form.
///
/// Ids missing from `effective` keep their default. Rejects unknown ids, invalid or reserved keys, and a key used by
/// two commands, naming `shortcuts.<id>` as the location.
pub fn overrides_from(
    effective: &BTreeMap<String, Option<String>>,
) -> Result<BTreeMap<String, Option<String>>, Error> {
    let fail = |id: &str, reason: String| Error {
        location: format!("shortcuts.{id}"),
        reason,
        invalid: true,
    };
    if let Some(id) = effective
        .keys()
        .find(|id| !COMMANDS.iter().any(|command| command.id == id.as_str()))
    {
        return Err(fail(id, "is not a command".to_owned()));
    }
    let mut owners: BTreeMap<String, &Command> = BTreeMap::new();
    let mut overrides = BTreeMap::new();
    for command in COMMANDS {
        let wanted = match effective.get(command.id) {
            Some(key) => key.as_deref(),
            None => command.key,
        };
        let key = wanted
            .map(canonical)
            .transpose()
            .map_err(|reason| fail(command.id, reason))?;
        if let Some(key) = &key {
            if RESERVED.contains(&key.as_str()) {
                return Err(fail(
                    command.id,
                    format!("{key} is reserved for zooming the page"),
                ));
            }
            if let Some(owner) = owners.insert(key.clone(), command) {
                return Err(fail(
                    command.id,
                    format!("{key} is already used by {}", owner.label),
                ));
            }
        }
        if key.as_deref() != command.key {
            overrides.insert(command.id.to_owned(), key);
        }
    }
    Ok(overrides)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(entries: &[(&str, Option<&str>)]) -> BTreeMap<String, Option<String>> {
        entries
            .iter()
            .map(|(id, key)| ((*id).to_owned(), key.map(str::to_owned)))
            .collect()
    }

    #[test]
    fn overrides_win_and_round_trip() {
        let none = effective(&BTreeMap::new());
        assert_eq!(none["save-workspace"].as_deref(), Some("Ctrl+S"));
        assert_eq!(none["new-workspace"], None);
        let all: BTreeMap<String, Option<String>> = none
            .iter()
            .map(|(id, key)| ((*id).to_owned(), key.clone()))
            .collect();
        assert!(
            overrides_from(&all).unwrap().is_empty(),
            "defaults are valid, unique and unreserved"
        );

        // Exit takes Ctrl+S: Save Workspace loses its default instead of sharing it.
        let chosen = map(&[
            ("exit", Some("ctrl+s")),
            ("new-workspace", Some("Ctrl+Shift+W")),
            ("about", None),
        ]);
        let bound = effective(&chosen);
        assert_eq!(bound["exit"].as_deref(), Some("ctrl+s"));
        assert_eq!(bound["save-workspace"], None);
        assert_eq!(bound["open-workspace"].as_deref(), Some("Ctrl+O"));

        let edited: BTreeMap<String, Option<String>> = bound
            .iter()
            .map(|(id, key)| ((*id).to_owned(), key.clone()))
            .collect();
        let saved = overrides_from(&edited).unwrap();
        assert_eq!(
            saved["exit"].as_deref(),
            Some("Ctrl+S"),
            "stored in canonical form"
        );
        assert_eq!(
            saved["save-workspace"], None,
            "an unbound default is stored as null"
        );
        assert_eq!(saved["new-workspace"].as_deref(), Some("Ctrl+Shift+W"));
        assert!(
            !saved.contains_key("about"),
            "a value equal to the default is not stored"
        );

        for (change, location, reason) in [
            (
                map(&[("exit", Some("Ctrl+S"))]),
                "shortcuts.exit",
                "Ctrl+S is already used by Save Workspace",
            ),
            (
                map(&[("exit", Some("Ctrl+0"))]),
                "shortcuts.exit",
                "reserved",
            ),
            (
                map(&[("exit", Some("Meta+Q"))]),
                "shortcuts.exit",
                "not a modifier",
            ),
            (map(&[("nope", None)]), "shortcuts.nope", "not a command"),
        ] {
            let mut full = all.clone();
            full.extend(change);
            let error = overrides_from(&full).unwrap_err();
            assert_eq!(error.location, location);
            assert!(error.reason.contains(reason), "{}", error.reason);
        }
    }

    #[test]
    fn canonical_keys_match_the_shared_vectors() {
        let vectors: Vec<serde_json::Value> =
            serde_json::from_str(include_str!("../../../fixtures/shortcuts/keys.json")).unwrap();
        for vector in vectors {
            let input = vector["input"].as_str().unwrap();
            match vector.get("canonical") {
                Some(expected) => assert_eq!(
                    canonical(input).as_deref(),
                    Ok(expected.as_str().unwrap()),
                    "{input:?}"
                ),
                None => assert!(canonical(input).is_err(), "{input:?} should be refused"),
            }
        }
    }
}
