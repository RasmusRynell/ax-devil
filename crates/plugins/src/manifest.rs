//! `ax-devil-plugin.json`: what a plugin says about itself, checked field by field.

use std::{
    ffi::OsString,
    fmt, fs,
    path::{Component, Path, PathBuf},
    time::Duration,
};

use handler_api::{Form, Settings, Values};
use scene::{MatchBasis, SceneVersion};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::{CommandHandler, LaneInputs, PROTOCOL, TaskHandler, ViewHandler, code, fields::Fields};

/// The file name a plugin's folder holds.
pub const MANIFEST_FILE: &str = "ax-devil-plugin.json";

/// The manifest version this build reads.
const MANIFEST_VERSION: u64 = 2;
/// Longest a manifest may be, in bytes.
const MAX_MANIFEST_BYTES: u64 = 1 << 20;
/// The idle timeout when a manifest names none, in seconds.
const DEFAULT_IDLE_S: u64 = 120;
/// The longest idle timeout a manifest may ask for: a day.
const MAX_IDLE_S: u64 = 86_400;

/// Why a manifest or a registry entry cannot be used: words for the user, naming the manifest field when there is
/// one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ManifestError {
    /// The path of the manifest field the problem is in, such as `handlers[1].options[0].kind`; `None` for the
    /// file as a whole.
    pub field: Option<String>,
    /// What is wrong.
    pub reason: String,
}

impl ManifestError {
    pub(crate) fn new(field: impl Into<String>, reason: impl Into<String>) -> Self {
        Self {
            field: Some(field.into()),
            reason: reason.into(),
        }
    }

    pub(crate) fn file(reason: impl Into<String>) -> Self {
        Self {
            field: None,
            reason: reason.into(),
        }
    }
}

impl ManifestError {
    /// The message with its first character capitalized, as the Plugins view, the CLI and the log say why a plugin
    /// did not load.
    pub fn capitalised(&self) -> String {
        let text = self.to_string();
        let mut chars = text.chars();
        chars.next().map_or_else(String::new, |first| {
            first.to_uppercase().chain(chars).collect()
        })
    }
}

impl fmt::Display for ManifestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.field {
            Some(field) => write!(f, "{field}: {}", self.reason),
            None => f.write_str(&self.reason),
        }
    }
}

impl std::error::Error for ManifestError {}

/// A validated manifest.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Manifest {
    /// The plugin id.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// Free text, informational.
    pub version: String,
    /// Program and arguments to run. Items naming a file or folder of the plugin are absolute, so the private
    /// working folder does not matter. Empty for a plugin of only views and commands, which has no program.
    pub command: Vec<OsString>,
    /// What the user may set for the whole plugin; the values are sent in every request the program gets.
    pub settings: Form,
    /// Environment variables passed through from the app besides the standard few.
    pub env: Vec<String>,
    /// Paths relative to the folder that identify the plugin's code.
    pub code: Vec<String>,
    /// How long the plugin may print nothing before it is killed.
    pub idle_timeout: Duration,
    /// What the plugin provides, in the order it lists them.
    pub handlers: Vec<Handler>,
    /// The handlers of a kind this build does not know, which take no id.
    pub skipped: Vec<SkippedHandler>,
    /// The manifest file.
    pub path: PathBuf,
    /// SHA-256 of the manifest text this was read from, so the code identity names the manifest that runs even if the
    /// file is edited afterwards.
    pub(crate) digest: [u8; 32],
}

/// A handler the manifest lists that this build cannot use: its kind is unknown, so nothing else of it is read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SkippedHandler {
    /// The `kind` the manifest gives.
    pub kind: String,
    /// The handler's `id` when it has one that is text.
    pub id: Option<String>,
    /// Why it is not used.
    pub reason: String,
}

/// One thing a plugin provides. A new kind of handler is a variant here and a `kind` value on the wire.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Handler {
    /// Turns an overlay file into Scenes.
    Decoder(DecoderHandler),
    /// Turns settings into a playlist.
    PlaylistResolver(ResolverHandler),
    /// Reads lanes and does something with them, such as an export.
    Task(TaskHandler),
    /// A page shown in a sandboxed frame.
    View(ViewHandler),
    /// A menu entry and shortcut that runs a handler of the plugin.
    Command(CommandHandler),
}

impl Handler {
    /// The handler id, which selects it (`--handler-type`).
    pub fn id(&self) -> &str {
        match self {
            Self::Decoder(decoder) => &decoder.id,
            Self::PlaylistResolver(resolver) => &resolver.id,
            Self::Task(task) => &task.id,
            Self::View(view) => &view.id,
            Self::Command(command) => &command.id,
        }
    }

    /// The handler's `kind` in the manifest.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Decoder(_) => "decoder",
            Self::PlaylistResolver(_) => "playlist_resolver",
            Self::Task(_) => "task",
            Self::View(_) => "view",
            Self::Command(_) => "command",
        }
    }

    /// Whether the app selects the handler by its id alone, so no other plugin or built-in may use it. A view or a
    /// command is named with its plugin's id in front, so its id only has to differ within the plugin.
    pub(crate) fn claims_id(&self) -> bool {
        !matches!(self, Self::View(_) | Self::Command(_))
    }

    /// Whether the handler runs the plugin's program, which the manifest must then give a `command` for.
    pub(crate) fn runs_program(&self) -> bool {
        matches!(
            self,
            Self::Decoder(_) | Self::PlaylistResolver(_) | Self::Task(_)
        )
    }
}

/// What a decoder's request carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecoderInput {
    /// The overlay file's path; one run reads the whole file.
    Path,
    /// Records, one per line on stdin, each answered before the next is sent: the lines of a file, or the documents of
    /// a live source.
    Records,
    /// Lanes of the same video, the host's bundle of them: a derived lane.
    Lanes,
}

/// A decoder the plugin declares.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DecoderHandler {
    /// The handler id.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What the request carries.
    pub input: DecoderInput,
    /// The lanes and video of a [`DecoderInput::Lanes`] decoder; empty for the others.
    pub inputs: LaneInputs,
    /// One sentence on what the decoder reads, if the manifest gives one.
    pub description: Option<String>,
    /// The Scene model version the decoder writes.
    pub scene_version: SceneVersion,
    /// File name endings the decoder reads: lowercase, with the dot; empty means any file.
    pub extensions: Vec<String>,
    /// The options the decoder takes.
    pub options: Form,
    /// What a lane may match the decoder's records to video frames by.
    pub bases: Vec<MatchBasis>,
    /// The basis a lane starts with, one of `bases`.
    pub default_basis: MatchBasis,
}

/// A playlist resolver the plugin declares.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolverHandler {
    /// The handler id, which `playlist <id>` and workspaces name the resolver by.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// One sentence on what the resolver does.
    pub description: String,
    /// The settings the resolver takes.
    pub settings: Form,
    /// Whether the plugin answers the `watch` operation before every resolve; false by default.
    pub watches: bool,
}

impl Manifest {
    /// The folder the manifest is in.
    pub fn folder(&self) -> &Path {
        self.path.parent().unwrap_or_else(|| Path::new("."))
    }

    /// The plugin's settings as the program gets them: `raw` as stored, with the defaults filled in. A missing
    /// required value is not an error here (the plugin loads so it can be configured; the Plugins view asks for it),
    /// and a stored value that no longer fits the form is dropped.
    pub fn settings_values(&self, raw: &Settings) -> Result<Values, ManifestError> {
        let mut lenient = self.settings.clone();
        for field in &mut lenient.fields {
            field.required = false;
        }
        lenient
            .validate(raw)
            .or_else(|error| {
                tracing::warn!(
                    "Plugin '{}': stored settings ignored: {}",
                    self.id,
                    error.reason
                );
                lenient.validate(&Settings::new())
            })
            .map_err(|error| ManifestError::new("settings", error.reason))
    }

    /// Reads and validates the manifest at `path`.
    pub fn load(path: &Path) -> Result<Self, ManifestError> {
        let unreadable = |error: std::io::Error| {
            ManifestError::file(format!("cannot read {}: {error}", path.display()))
        };
        let size = fs::metadata(path).map_err(unreadable)?.len();
        if size > MAX_MANIFEST_BYTES {
            return Err(ManifestError::file("the manifest is larger than 1 MiB"));
        }
        let text = fs::read_to_string(path).map_err(unreadable)?;
        let document: Value = serde_json::from_str(&text)
            .map_err(|error| ManifestError::file(format!("not valid JSON: {error}")))?;
        let Value::Object(mut fields) = document else {
            return Err(ManifestError::file("the manifest must be a JSON object"));
        };
        let mut read = Fields::new(&mut fields, String::new());
        let manifest = Self::from_fields(&mut read, path, Sha256::digest(text.as_bytes()).into())?;
        read.finish()?;
        code::collect(&manifest, code::LIMITS)?;
        Ok(manifest)
    }

    fn from_fields(
        read: &mut Fields<'_>,
        path: &Path,
        digest: [u8; 32],
    ) -> Result<Self, ManifestError> {
        match read.number("manifest")? {
            MANIFEST_VERSION => {}
            newer if newer > MANIFEST_VERSION => {
                return Err(ManifestError::new(
                    "manifest",
                    format!("{newer} was written for a newer ax-devil"),
                ));
            }
            other => {
                return Err(ManifestError::new(
                    "manifest",
                    format!(
                        "{other} is not supported; this ax-devil reads manifest {MANIFEST_VERSION}"
                    ),
                ));
            }
        }
        let id = read.id("id")?;
        let name = read.non_empty("name")?;
        let version = read.non_empty("version")?;
        let protocol = read.number("protocol")?;
        if protocol != u64::from(PROTOCOL) {
            return Err(ManifestError::new(
                "protocol",
                format!("{protocol} is not supported; this ax-devil speaks protocol {PROTOCOL}"),
            ));
        }
        let folder = path.parent().unwrap_or_else(|| Path::new("."));
        let program = read.strings("command", false)?;
        let env = read.strings("env", false)?.unwrap_or_default();
        for name in &env {
            if name.starts_with("AX_DEVIL_") {
                return Err(ManifestError::new(
                    "env",
                    format!("{name} is refused: AX_DEVIL_ variables hold credentials"),
                ));
            }
            if name.is_empty() || name.contains(['=', '\0']) {
                return Err(ManifestError::new(
                    "env",
                    format!("'{name}' is not a variable name"),
                ));
            }
        }
        let code = read
            .strings("code", false)?
            .unwrap_or_else(|| vec![".".to_owned()]);
        let idle_timeout = Duration::from_secs(match read.optional_number("idle_timeout_s")? {
            None => DEFAULT_IDLE_S,
            Some(seconds @ 1..=MAX_IDLE_S) => seconds,
            Some(other) => {
                return Err(ManifestError::new(
                    "idle_timeout_s",
                    format!("{other} is outside 1 to {MAX_IDLE_S} seconds"),
                ));
            }
        });
        let settings = read.form("settings", false)?;
        let (handlers, skipped) = read.handlers(folder)?;
        // Only a plugin with something to run needs a program: one of views and commands has none.
        let command = match program {
            Some(items) => command(&items, folder)?,
            None if handlers.iter().any(Handler::runs_program) => {
                return Err(ManifestError::new("command", "is required"));
            }
            None => Vec::new(),
        };
        Ok(Self {
            id,
            name,
            version,
            command,
            settings,
            env,
            code,
            idle_timeout,
            handlers,
            skipped,
            path: path.to_path_buf(),
            digest,
        })
    }
}

pub(crate) fn valid_id(id: &str) -> bool {
    let mut chars = id.chars();
    id.len() <= 64
        && chars
            .next()
            .is_some_and(|first| first.is_ascii_lowercase() || first.is_ascii_digit())
        && chars
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-'))
}

/// The program and arguments with the plugin's own files made absolute.
///
/// The program resolves against the folder when it contains a `/`, else is looked up on the plugin's `PATH`.
/// Later items name a file or folder of the plugin (`main.py`) or are passed as they are (`-u`).
fn command(items: &[String], folder: &Path) -> Result<Vec<OsString>, ManifestError> {
    let Some((program, rest)) = items.split_first() else {
        return Err(ManifestError::new("command", "must name a program"));
    };
    if items.iter().any(String::is_empty) {
        return Err(ManifestError::new(
            "command",
            "must not contain an empty item",
        ));
    }
    let mut resolved = Vec::with_capacity(items.len());
    if program.contains('/') {
        let program_path = folder.join(program);
        if !is_executable(&program_path) {
            return Err(ManifestError::new(
                "command",
                format!("{} is not an executable file", program_path.display()),
            ));
        }
        resolved.push(program_path.into_os_string());
    } else {
        resolved.push(program.into());
    }
    for item in rest {
        let own = folder.join(item);
        resolved.push(if !item.starts_with('-') && own.exists() {
            own.into_os_string()
        } else {
            item.into()
        });
    }
    Ok(resolved)
}

fn is_executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        metadata.is_file()
    }
}

/// Whether `path` stays inside its folder: relative, with no `..`.
pub(crate) fn is_inside(path: &Path) -> bool {
    path.components()
        .all(|part| matches!(part, Component::Normal(_) | Component::CurDir))
}
