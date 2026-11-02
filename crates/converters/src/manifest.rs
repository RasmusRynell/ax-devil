//! `ax-devil-converter.json`: what a converter says about itself, checked field by field.

use std::{
    collections::HashSet,
    ffi::OsString,
    fmt, fs,
    path::{Component, Path, PathBuf},
    time::Duration,
};

use scene::{SCENE_VERSION, SceneVersion};
use scene_decoders::MatchBasis;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::{PROTOCOL, code};

/// The file name a converter's folder holds.
pub const MANIFEST_FILE: &str = "ax-devil-converter.json";

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
    /// The manifest field the problem is in; `None` for the file as a whole.
    pub field: Option<&'static str>,
    /// What is wrong.
    pub reason: String,
}

impl ManifestError {
    pub(crate) fn new(field: &'static str, reason: impl Into<String>) -> Self {
        Self {
            field: Some(field),
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

impl fmt::Display for ManifestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.field {
            Some(field) => write!(f, "{field}: {}", self.reason),
            None => f.write_str(&self.reason),
        }
    }
}

impl std::error::Error for ManifestError {}

/// What kind of value an option holds, and so which control it gets.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OptionKind {
    /// Free text.
    Text,
    /// A file path.
    File,
    /// A folder path.
    Folder,
}

/// One option a converter declares; values are always text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OptionField {
    /// The key in the options and in the request.
    pub key: String,
    /// What the user reads.
    pub label: String,
    /// Which control it gets.
    pub kind: OptionKind,
    /// Whether decoding fails without a value (and without a default).
    pub required: bool,
    /// The value used when the overlay gives none.
    pub default: Option<String>,
}

/// A validated manifest.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Manifest {
    /// The plugin id and the handler id.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// Free text, informational.
    pub version: String,
    /// The Scene model version the converter writes.
    pub scene_version: SceneVersion,
    /// File name endings the converter reads: lowercase, with the dot; may be empty.
    pub extensions: Vec<String>,
    /// Program and arguments to run; the overlay path is appended. Items naming a file or folder of the converter
    /// are absolute, so the private working folder does not matter.
    pub command: Vec<OsString>,
    /// Environment variables passed through from the app besides the standard few.
    pub env: Vec<String>,
    /// Paths relative to the folder that identify the converter's code.
    pub code: Vec<String>,
    /// How long the converter may print nothing before it is killed.
    pub idle_timeout: Duration,
    /// The options the converter takes.
    pub options: Vec<OptionField>,
    /// What a lane may match the converter's records to video frames by.
    pub bases: Vec<MatchBasis>,
    /// The basis a lane starts with, one of `bases`.
    pub default_basis: MatchBasis,
    /// The manifest file.
    pub path: PathBuf,
    /// SHA-256 of the manifest text this was read from, so the code identity names the manifest that runs even if the
    /// file is edited afterwards.
    pub(crate) digest: [u8; 32],
}

impl Manifest {
    /// The folder the manifest is in.
    pub fn folder(&self) -> &Path {
        self.path.parent().unwrap_or_else(|| Path::new("."))
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
        let Value::Object(fields) = document else {
            return Err(ManifestError::file("the manifest must be a JSON object"));
        };
        Self::from_fields(fields, path, Sha256::digest(text.as_bytes()).into())
    }

    fn from_fields(
        mut fields: Map<String, Value>,
        path: &Path,
        digest: [u8; 32],
    ) -> Result<Self, ManifestError> {
        let mut read = Fields(&mut fields);
        match read.number("manifest")? {
            1 => {}
            newer if newer > 1 => {
                return Err(ManifestError::new(
                    "manifest",
                    format!("{newer} was written for a newer ax-devil"),
                ));
            }
            other => {
                return Err(ManifestError::new(
                    "manifest",
                    format!("{other} is not a manifest version"),
                ));
            }
        }
        if read.string("kind")? != "file" {
            return Err(ManifestError::new("kind", "only \"file\" is supported"));
        }
        let id = read.string("id")?;
        if !valid_id(&id) {
            return Err(ManifestError::new(
                "id",
                format!(
                    "'{id}' must be 1-64 characters of a-z, 0-9, '.', '_' or '-', starting with a letter or digit"
                ),
            ));
        }
        let name = read.non_empty("name")?;
        let version = read.non_empty("version")?;
        let protocol = read.number("protocol")?;
        if protocol != u64::from(PROTOCOL) {
            return Err(ManifestError::new(
                "protocol",
                format!("{protocol} is not supported; this ax-devil speaks protocol {PROTOCOL}"),
            ));
        }
        let scene_version = scene_version(&read.string("scene_version")?)?;
        let extensions = read.strings("extensions", false)?.unwrap_or_default();
        for extension in &extensions {
            if extension.len() < 2
                || !extension.starts_with('.')
                || *extension != extension.to_lowercase()
                || extension.contains(['/', '\\'])
                || extension.contains(char::is_whitespace)
            {
                return Err(ManifestError::new(
                    "extensions",
                    format!(
                        "'{extension}' must be lowercase and start with a dot, such as .acme.jsonl"
                    ),
                ));
            }
        }
        let folder = path.parent().unwrap_or_else(|| Path::new("."));
        let command = command(&read.strings("command", true)?.unwrap_or_default(), folder)?;
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
        let options = read.options()?;
        let (bases, default_basis) = read.bases()?;
        if let Some(unknown) = fields.keys().next() {
            return Err(ManifestError::new(
                "manifest",
                format!("unknown field '{unknown}'"),
            ));
        }
        let manifest = Self {
            id,
            name,
            version,
            scene_version,
            extensions,
            command,
            env,
            code,
            idle_timeout,
            options,
            bases,
            default_basis,
            path: path.to_path_buf(),
            digest,
        };
        code::collect(&manifest, code::LIMITS)?;
        Ok(manifest)
    }
}

fn valid_id(id: &str) -> bool {
    let mut chars = id.chars();
    id.len() <= 64
        && chars
            .next()
            .is_some_and(|first| first.is_ascii_lowercase() || first.is_ascii_digit())
        && chars
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-'))
}

fn scene_version(text: &str) -> Result<SceneVersion, ManifestError> {
    let number = |part: Option<&str>| part.and_then(|part| part.parse::<u16>().ok());
    let mut parts = text.split('.');
    let (Some(major), Some(minor), None) =
        (number(parts.next()), number(parts.next()), parts.next())
    else {
        return Err(ManifestError::new(
            "scene_version",
            format!("'{text}' is not a version such as {SCENE_VERSION}"),
        ));
    };
    let declared = SceneVersion { major, minor };
    if SCENE_VERSION.accepts(declared) {
        Ok(declared)
    } else {
        Err(ManifestError::new(
            "scene_version",
            format!("{declared} is not readable by this ax-devil (Scene version {SCENE_VERSION})"),
        ))
    }
}

/// The program and arguments with the converter's own files made absolute.
///
/// The program resolves against the folder when it contains a `/`, else is looked up on the converter's `PATH`.
/// Later items name a file or folder of the converter (`convert.py`) or are passed as they are (`-u`).
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

/// Typed reads of the manifest's fields; each read takes the field out, so what is left is unknown.
struct Fields<'a>(&'a mut Map<String, Value>);

impl Fields<'_> {
    fn take(&mut self, key: &'static str) -> Result<Value, ManifestError> {
        self.0
            .remove(key)
            .ok_or_else(|| ManifestError::new(key, "is required"))
    }

    fn string(&mut self, key: &'static str) -> Result<String, ManifestError> {
        match self.take(key)? {
            Value::String(text) => Ok(text),
            _ => Err(ManifestError::new(key, "must be text")),
        }
    }

    fn non_empty(&mut self, key: &'static str) -> Result<String, ManifestError> {
        let text = self.string(key)?;
        if text.trim().is_empty() {
            return Err(ManifestError::new(key, "must not be empty"));
        }
        Ok(text)
    }

    fn number(&mut self, key: &'static str) -> Result<u64, ManifestError> {
        self.take(key)?
            .as_u64()
            .ok_or_else(|| ManifestError::new(key, "must be a whole number"))
    }

    fn optional_number(&mut self, key: &'static str) -> Result<Option<u64>, ManifestError> {
        if self.0.contains_key(key) {
            self.number(key).map(Some)
        } else {
            Ok(None)
        }
    }

    /// A list of text; absent is `None` unless `required`, which refuses an empty list as well.
    fn strings(
        &mut self,
        key: &'static str,
        required: bool,
    ) -> Result<Option<Vec<String>>, ManifestError> {
        if !required && !self.0.contains_key(key) {
            return Ok(None);
        }
        let Value::Array(items) = self.take(key)? else {
            return Err(ManifestError::new(key, "must be a list of text"));
        };
        let texts: Vec<String> = items
            .into_iter()
            .map(|item| match item {
                Value::String(text) => Ok(text),
                _ => Err(ManifestError::new(key, "must be a list of text")),
            })
            .collect::<Result<_, _>>()?;
        if required && texts.is_empty() {
            return Err(ManifestError::new(key, "must not be empty"));
        }
        Ok(Some(texts))
    }

    /// The bases and the default one: `["frame"]` and the first listed when absent.
    fn bases(&mut self) -> Result<(Vec<MatchBasis>, MatchBasis), ManifestError> {
        let basis = |key: &'static str, text: &str| match text {
            "frame" => Ok(MatchBasis::Frame),
            "time" => Ok(MatchBasis::Time),
            other => Err(ManifestError::new(
                key,
                format!("'{other}' is not frame or time"),
            )),
        };
        let bases = match self.strings("bases", false)? {
            None => vec![MatchBasis::Frame],
            Some(texts) => texts
                .iter()
                .map(|text| basis("bases", text))
                .collect::<Result<Vec<_>, _>>()?,
        };
        if bases.is_empty() || (1..bases.len()).any(|i| bases[..i].contains(&bases[i])) {
            return Err(ManifestError::new(
                "bases",
                "must list frame, time or both, once each",
            ));
        }
        let default = match self.0.remove("default_basis") {
            None => bases[0],
            Some(Value::String(text)) => basis("default_basis", &text)?,
            Some(_) => return Err(ManifestError::new("default_basis", "must be text")),
        };
        if !bases.contains(&default) {
            return Err(ManifestError::new("default_basis", "must be one of bases"));
        }
        Ok((bases, default))
    }

    fn options(&mut self) -> Result<Vec<OptionField>, ManifestError> {
        let Some(options) = self.0.remove("options") else {
            return Ok(Vec::new());
        };
        let Value::Array(items) = options else {
            return Err(ManifestError::new("options", "must be a list"));
        };
        let mut seen = HashSet::new();
        items
            .into_iter()
            .map(|item| {
                let field = option(item)?;
                if seen.insert(field.key.clone()) {
                    Ok(field)
                } else {
                    Err(ManifestError::new(
                        "options",
                        format!("the key '{}' is declared twice", field.key),
                    ))
                }
            })
            .collect()
    }
}

fn option(item: Value) -> Result<OptionField, ManifestError> {
    let bad = |reason: String| ManifestError::new("options", reason);
    let Value::Object(mut fields) = item else {
        return Err(bad("every option must be an object".to_owned()));
    };
    let mut text = |key: &str| match fields.remove(key) {
        Some(Value::String(text)) if !text.is_empty() => Ok(text),
        _ => Err(bad(format!("'{key}' must be non-empty text"))),
    };
    let key = text("key")?;
    let label = text("label")?;
    let kind = match text("kind")?.as_str() {
        "text" => OptionKind::Text,
        "file" => OptionKind::File,
        "folder" => OptionKind::Folder,
        other => {
            return Err(bad(format!(
                "{key}: kind '{other}' is not text, file or folder"
            )));
        }
    };
    if !key
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
    {
        return Err(bad(format!("'{key}' must be letters, digits, '_' or '-'")));
    }
    let required = match fields.remove("required") {
        None => false,
        Some(Value::Bool(required)) => required,
        Some(_) => return Err(bad(format!("{key}: 'required' must be true or false"))),
    };
    let default = match fields.remove("default") {
        None | Some(Value::Null) => None,
        Some(Value::String(default)) => Some(default),
        Some(_) => return Err(bad(format!("{key}: 'default' must be text"))),
    };
    if let Some(unknown) = fields.keys().next() {
        return Err(bad(format!("{key}: unknown field '{unknown}'")));
    }
    Ok(OptionField {
        key,
        label,
        kind,
        required,
        default,
    })
}

/// Whether `path` stays inside its folder: relative, with no `..`.
pub(crate) fn is_inside(path: &Path) -> bool {
    path.components()
        .all(|part| matches!(part, Component::Normal(_) | Component::CurDir))
}
