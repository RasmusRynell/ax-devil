//! The keyboard commands, their default keys, and the rules for personal key overrides.
//!
//! Pure data and functions: the file I/O and the live state live in the crate root. A key is written
//! `Meta+Ctrl+Alt+Shift+<key>` (modifiers in that order, omitted when unused); the frontend matches events against the
//! same grammar. `<key>` is a letter (upper case), a digit, one ASCII symbol, `F1`..`F24`, or one of [`NAMED_KEYS`].

use std::{borrow::Cow, collections::BTreeMap};

use crate::{
    Error,
    table::{LANE_NAMES_DESCRIPTION, OBJECT_DETAILS_DESCRIPTION},
};
use serde::Serialize;

/// The group a command is listed under in the shortcuts dialog.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Category {
    /// Workspace, windows, settings and diagnostics.
    Application,
    /// Moving between playlist entries.
    Navigation,
    /// Stepping, speed and play state.
    Playback,
    /// Zoom, panels, overlays and what is shown.
    View,
    /// What plugins add.
    Plugins,
}

/// One command the user can run from the menus or the keyboard.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Command {
    /// Stable id, used in the settings file.
    pub id: Cow<'static, str>,
    /// Menu and dialog label.
    pub label: Cow<'static, str>,
    /// The dialog section the command is listed under.
    pub category: Category,
    /// The default key in canonical form, or `None` when the command ships unbound.
    pub key: Option<Cow<'static, str>>,
    /// Whether holding the key repeats the command.
    pub repeats: bool,
    /// What the command does, shown as its menu item's tooltip; `None` shows none.
    pub description: Option<&'static str>,
}

const fn command(
    id: &'static str,
    label: &'static str,
    category: Category,
    key: Option<&'static str>,
    repeats: bool,
) -> Command {
    Command {
        id: Cow::Borrowed(id),
        label: Cow::Borrowed(label),
        category,
        key: match key {
            Some(key) => Some(Cow::Borrowed(key)),
            None => None,
        },
        repeats,
        description: None,
    }
}

/// `command` with a tooltip.
const fn described(mut command: Command, description: &'static str) -> Command {
    command.description = Some(description);
    command
}

/// Every command, in menu order.
pub const COMMANDS: &[Command] = &[
    command(
        "new-workspace",
        "New Workspace",
        Category::Application,
        None,
        false,
    ),
    command(
        "open-workspace",
        "Open Workspace",
        Category::Application,
        Some("Ctrl+O"),
        false,
    ),
    command(
        "save-workspace",
        "Save Workspace",
        Category::Application,
        Some("Ctrl+S"),
        false,
    ),
    command(
        "save-workspace-as",
        "Save Workspace As",
        Category::Application,
        Some("Ctrl+Shift+S"),
        false,
    ),
    command(
        "add-video",
        "Add Video",
        Category::Application,
        Some("Ctrl+N"),
        false,
    ),
    command(
        "add-live-stream",
        "Add Live Stream",
        Category::Application,
        Some("Ctrl+L"),
        false,
    ),
    command(
        "add-playlist",
        "Add Playlist",
        Category::Application,
        Some("Ctrl+Shift+N"),
        false,
    ),
    command(
        "keyboard-shortcuts",
        "Keyboard Shortcuts",
        Category::Application,
        Some("Ctrl+K"),
        false,
    ),
    command(
        "settings",
        "Settings",
        Category::Application,
        Some("Ctrl+,"),
        false,
    ),
    command("exit", "Exit", Category::Application, Some("Ctrl+Q"), false),
    command(
        "fullscreen",
        "Fullscreen",
        Category::View,
        Some("F11"),
        false,
    ),
    command(
        "lane-fullscreen",
        "Toggle Lane Fullscreen",
        Category::View,
        Some("F"),
        false,
    ),
    command("zoom-in", "Zoom In", Category::View, Some("="), true),
    command("zoom-out", "Zoom Out", Category::View, Some("-"), true),
    command("zoom-reset", "Reset Zoom", Category::View, Some("0"), false),
    command(
        "media-tools",
        "Toggle Media Tools Panel",
        Category::View,
        Some("Ctrl+B"),
        false,
    ),
    command("sidebar", "Sidebar", Category::View, Some("Ctrl+\\"), false),
    command(
        "play-pause",
        "Play / Pause",
        Category::Playback,
        Some("Space"),
        false,
    ),
    command(
        "step-forward",
        "Step Forward 1 Frame",
        Category::Playback,
        Some("."),
        true,
    ),
    command(
        "step-back",
        "Step Backward 1 Frame",
        Category::Playback,
        Some(","),
        true,
    ),
    command(
        "step-forward-10",
        "Step Forward 10 Frames",
        Category::Playback,
        Some("Right"),
        true,
    ),
    command(
        "step-back-10",
        "Step Backward 10 Frames",
        Category::Playback,
        Some("Left"),
        true,
    ),
    command(
        "previous-entry",
        "Previous Playlist Entry",
        Category::Navigation,
        Some("Ctrl+Left"),
        false,
    ),
    command(
        "next-entry",
        "Next Playlist Entry",
        Category::Navigation,
        Some("Ctrl+Right"),
        false,
    ),
    command(
        "speed-up",
        "Playback Speed Up",
        Category::Playback,
        Some("]"),
        true,
    ),
    command(
        "speed-down",
        "Playback Speed Down",
        Category::Playback,
        Some("["),
        true,
    ),
    described(
        command("lane-names", "Show Lane Names", Category::View, None, false),
        LANE_NAMES_DESCRIPTION,
    ),
    described(
        command(
            "object-details",
            "Object Hover and Click Details",
            Category::View,
            None,
            false,
        ),
        OBJECT_DETAILS_DESCRIPTION,
    ),
    command(
        "info-overlay",
        "Toggle Info Overlay",
        Category::View,
        Some("I"),
        false,
    ),
    command(
        "add-bookmark",
        "Add Bookmark",
        Category::View,
        Some("M"),
        false,
    ),
    command(
        "export-video",
        "Export Video",
        Category::View,
        Some("Ctrl+E"),
        false,
    ),
    command("plugins", "Plugins", Category::Application, None, false),
    command(
        "quick-setup",
        "Quick Setup",
        Category::Application,
        None,
        false,
    ),
    command("about", "About", Category::Application, None, false),
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
/// Modifiers are `Meta`, `Ctrl`, `Alt`, `Shift` (any case, any order, each at most once) and come out in that order. Shift
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
    let mut held = [false; 4];
    for part in modifiers.map_or_else(Vec::new, |rest| rest.split('+').collect()) {
        let slot = ["meta", "ctrl", "alt", "shift"]
            .iter()
            .position(|modifier| part.eq_ignore_ascii_case(modifier))
            .ok_or_else(|| format!("\"{part}\" is not a modifier; use Meta, Ctrl, Alt or Shift"))?;
        if std::mem::replace(&mut held[slot], true) {
            return Err(format!("{part} is given twice"));
        }
    }
    let name = canonical_name(name)?;
    let symbol = name.len() == 1 && !name.as_bytes()[0].is_ascii_alphanumeric();
    if symbol && held[3] {
        return Err(format!("Shift cannot be combined with {name}"));
    }
    let mut out = String::new();
    for (held, modifier) in held.iter().zip(["Meta", "Ctrl", "Alt", "Shift"]) {
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

/// The built-in commands followed by `plugins`, each with the default key it may keep.
///
/// A plugin default that is invalid, reserved, equals a built-in default or is already another command's default
/// ships unbound, so a plugin never takes a key from the app or from an earlier plugin.
pub fn with_plugins(plugins: impl IntoIterator<Item = Command>) -> Vec<Command> {
    let mut commands = COMMANDS.to_vec();
    for mut plugin in plugins {
        plugin.key = plugin
            .key
            .as_deref()
            .and_then(|key| canonical(key).ok())
            .filter(|key| {
                !RESERVED.contains(&key.as_str())
                    && !commands
                        .iter()
                        .any(|other| other.key.as_deref() == Some(key.as_str()))
            })
            .map(Cow::Owned);
        commands.push(plugin);
    }
    commands
}

/// The key each of `commands` is bound to once `overrides` (id to key, `None` = unbound) are applied.
///
/// An override wins; a default whose key an override uses becomes unbound, so a key the user chose is never shared
/// with a default added in a later release. Overrides for unknown ids change nothing.
pub fn effective(
    commands: &[Command],
    overrides: &BTreeMap<String, Option<String>>,
) -> BTreeMap<String, Option<String>> {
    let taken: Vec<String> = commands
        .iter()
        .filter_map(|command| canonical(overrides.get(command.id.as_ref())?.as_deref()?).ok())
        .collect();
    commands
        .iter()
        .map(|command| {
            let key = match overrides.get(command.id.as_ref()) {
                Some(key) => key.clone(),
                None => command
                    .key
                    .as_deref()
                    .filter(|key| !taken.iter().any(|taken| taken == key))
                    .map(str::to_owned),
            };
            (command.id.to_string(), key)
        })
        .collect()
}

/// The overrides that produce `effective`: only the commands whose key differs from the default, in canonical form.
///
/// Ids missing from `effective` keep their default. Rejects ids not in `commands`, invalid or reserved keys, and a key
/// used by two commands, naming `shortcuts.<id>` as the location.
pub fn overrides_from(
    commands: &[Command],
    effective: &BTreeMap<String, Option<String>>,
) -> Result<BTreeMap<String, Option<String>>, Error> {
    let fail = |id: &str, reason: String| Error {
        location: format!("shortcuts.{id}"),
        reason,
        invalid: true,
        forbidden: false,
    };
    if let Some(id) = effective
        .keys()
        .find(|id| !commands.iter().any(|command| command.id == id.as_str()))
    {
        return Err(fail(id, "is not a command".to_owned()));
    }
    let mut owners: BTreeMap<String, &Command> = BTreeMap::new();
    let mut overrides = BTreeMap::new();
    for command in commands {
        let wanted = match effective.get(command.id.as_ref()) {
            Some(key) => key.as_deref(),
            None => command.key.as_deref(),
        };
        let key = wanted
            .map(canonical)
            .transpose()
            .map_err(|reason| fail(&command.id, reason))?;
        if let Some(key) = &key {
            if RESERVED.contains(&key.as_str()) {
                return Err(fail(
                    &command.id,
                    format!("{key} is reserved for zooming the page"),
                ));
            }
            if let Some(owner) = owners.insert(key.clone(), command) {
                return Err(fail(
                    &command.id,
                    format!("{key} is already used by {}", owner.label),
                ));
            }
        }
        if key.as_deref() != command.key.as_deref() {
            overrides.insert(command.id.to_string(), key);
        }
    }
    Ok(overrides)
}

#[cfg(test)]
mod tests;
