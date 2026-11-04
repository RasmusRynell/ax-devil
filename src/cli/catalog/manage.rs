//! The `catalog` commands that work on the library: `list`, `new` and `use`, and how a command names a catalog.

use super::{Report, Target, check_target};
use crate::paths::Paths;
use catalogs::{CatalogId, CatalogInfo, DEFAULT_SETTING, Library, MAX_CATALOG_BYTES, Served};
use settings::Settings;
use std::{
    fmt::Write,
    fs,
    io::Read as _,
    path::{Path, PathBuf},
};

/// Where the library commands read and write: the user's catalog folder and the settings file.
pub struct Home {
    /// The folder of the user's catalogs.
    pub folder: PathBuf,
    /// The settings, which hold the default catalog.
    pub settings: Settings,
}

impl Home {
    /// The folder and settings of this launch; `config` and `cache_dir` are the global overrides.
    pub fn resolve(config: Option<PathBuf>, cache_dir: Option<PathBuf>) -> Result<Self, String> {
        let paths = Paths::resolve(&|name| std::env::var_os(name), config, cache_dir)?;
        let settings = Settings::load(&paths.config_file).map_err(|error| error.to_string())?;
        Ok(Self {
            folder: crate::cli::catalog_folder(&paths, &settings),
            settings,
        })
    }

    /// The catalog new lanes start with: the setting, else Standard. A setting that names no catalog is an error.
    pub fn default_id(&self, library: &Library) -> Result<CatalogId, String> {
        let value = self.settings.state().resolved(DEFAULT_SETTING);
        match value.as_ref().and_then(|value| value.as_str()) {
            None | Some("") => Ok(library.default_id()),
            Some(id) => CatalogId::parse(id)
                .ok_or_else(|| format!("{DEFAULT_SETTING} is '{id}', which is not a catalog id")),
        }
    }

    /// The file a user catalog is, or the id of a built-in.
    fn shown(&self, id: &CatalogId) -> String {
        match id {
            CatalogId::BuiltIn(_) => id.to_string(),
            CatalogId::User(file) => self.folder.join(file).display().to_string(),
        }
    }

    /// The catalog `id` as `check` reads it.
    pub fn target(&self, id: CatalogId) -> Target {
        match id {
            CatalogId::BuiltIn(name) => Target::BuiltIn(name),
            CatalogId::User(file) => Target::File(self.folder.join(file)),
        }
    }
}

/// `catalog list`: the catalogs, one per line; `*` marks the default for new lanes.
pub fn list(home: &Home) -> Report {
    let library = Library::with_folder(home.folder.clone());
    let default = home.default_id(&library).ok();
    let mut text = String::new();
    for info in library.documents() {
        let mark = if default.as_ref() == Some(&info.id) {
            '*'
        } else if info.problem.is_some() {
            '!'
        } else {
            ' '
        };
        let note = match (&info.id, &info.problem) {
            (_, Some(problem)) => format!("  (does not load: {problem})"),
            (CatalogId::BuiltIn(_), None) => "  (built-in, read-only)".to_owned(),
            (CatalogId::User(_), None) => String::new(),
        };
        let name = match (&info.id, &info.problem) {
            (CatalogId::User(file), Some(_)) => file,
            _ => &info.name,
        };
        let _ = writeln!(text, "{mark} {name:<28} {}{note}", home.shown(&info.id));
    }
    Report { text, ok: true }
}

/// `catalog new NAME [--from CATALOG]`: a copy of the catalog in the folder, whose file is printed. The source is a
/// catalog the folder lists or any catalog file.
pub fn new(home: &Home, name: &str, from: Option<&Path>) -> Result<Report, String> {
    let library = Library::with_folder(home.folder.clone());
    let source = match from {
        Some(from) => named(from, home, &library)?,
        None => Named::Listed(library.default_id()),
    };
    let created = match source {
        Named::Listed(source) => {
            if let Some(refused) = checked(home, &library, &source)? {
                return Ok(refused);
            }
            library.copy(&source, name)
        }
        Named::Outside(file) => {
            let text = read_outside(file)?;
            let report = check_text(&text, file)?;
            if !report.ok {
                return Ok(report);
            }
            library.copy_document(&text, name)
        }
    }
    .map_err(|error| error.reason)?;
    Ok(Report {
        text: format!("{}\n", home.shown(&created)),
        ok: true,
    })
}

/// `catalog use CATALOG`: stores the catalog as the default for new lanes. `catalog use` writes the settings file; a
/// running app keeps the settings it loaded, so the choice applies at its next start, and any settings save in the
/// running app first overwrites it.
pub fn use_catalog(home: &Home, catalog: &Path) -> Result<Report, String> {
    let library = Library::with_folder(home.folder.clone());
    let Named::Listed(id) = named(catalog, home, &library)? else {
        return Err(outside(catalog, home));
    };
    if let Some(refused) = checked(home, &library, &id)? {
        return Ok(refused);
    }
    let stored = if id == library.default_id() {
        String::new()
    } else {
        id.to_string()
    };
    home.settings
        .update(DEFAULT_SETTING, |_| Some(stored.into()))
        .map_err(|error| error.to_string())?;
    Ok(Report {
        text: format!("Default catalog: {}\n", home.shown(&id)),
        ok: true,
    })
}

/// The check a user catalog must pass before it is copied or made the default: its problems, or `None`. Built-ins
/// are known to be good.
fn checked(home: &Home, library: &Library, id: &CatalogId) -> Result<Option<Report>, String> {
    if matches!(id, CatalogId::BuiltIn(_)) {
        return Ok(None);
    }
    let report = check_listed(home, library, id)?;
    Ok((!report.ok).then_some(report))
}

/// The check of the user catalog `id`, once the library serves it: a file the app would not serve (a link, a file too
/// large to read) is refused with the library's reason, however well its content checks.
pub fn check_listed(home: &Home, library: &Library, id: &CatalogId) -> Result<Report, String> {
    match library.document(id) {
        Served::Document { .. } => check_target(&home.target(id.clone())),
        Served::Unreadable { problem, .. } => Err(format!("{id} cannot be used: {problem}")),
        Served::Missing { .. } => Err(format!("{id} is not in {}", home.folder.display())),
    }
}

/// What a command's catalog argument names.
enum Named<'a> {
    /// A catalog the folder lists.
    Listed(CatalogId),
    /// A file that is not in the folder.
    Outside(&'a Path),
}

/// What `given` names: `built-in:<name>`, `user:<file>` of the folder, or a path to a file. A file in the folder is
/// found by its identity and named as the folder lists it, whatever spelling the path used; any other file is outside.
fn named<'a>(given: &'a Path, home: &Home, library: &Library) -> Result<Named<'a>, String> {
    let listed = library.documents();
    if let Some(id) = given.to_str().and_then(CatalogId::parse) {
        return if listed.iter().any(|info| info.id == id) {
            Ok(Named::Listed(id))
        } else {
            Err(format!("{id} is not in {}", home.folder.display()))
        };
    }
    fs::symlink_metadata(given)
        .map_err(|error| format!("Cannot read {}: {error}", given.display()))?;
    let files: Vec<&String> = listed.iter().filter_map(user_file).collect();
    let same: Vec<&String> = files
        .into_iter()
        .filter(|file| same_file(&home.folder.join(file), given))
        .collect();
    let typed = given.file_name().and_then(|name| name.to_str());
    Ok(same
        .iter()
        .find(|file| Some(file.as_str()) == typed)
        .or(same.first())
        .map_or(Named::Outside(given), |file| {
            Named::Listed(CatalogId::User((*file).clone()))
        }))
}

/// Why a file outside the catalog folder cannot be made the default.
fn outside(given: &Path, home: &Home) -> String {
    format!(
        "{} is not in the catalog folder {}; only catalogs that folder lists can be made the default",
        given.display(),
        home.folder.display()
    )
}

/// The text of the catalog file `file`: a regular file of at most [`MAX_CATALOG_BYTES`] that is UTF-8.
fn read_outside(file: &Path) -> Result<String, String> {
    let failed =
        |reason: &dyn std::fmt::Display| format!("Cannot read {}: {reason}", file.display());
    let opened = fs::File::open(file).map_err(|error| failed(&error))?;
    if !opened.metadata().map_err(|error| failed(&error))?.is_file() {
        return Err(failed(&"not a file"));
    }
    let mut bytes = Vec::new();
    opened
        .take(MAX_CATALOG_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| failed(&error))?;
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return Err(failed(&format!(
            "over {MAX_CATALOG_BYTES} bytes, the most a catalog may have"
        )));
    }
    String::from_utf8(bytes).map_err(|_| failed(&"not UTF-8 text"))
}

/// The check of the catalog document `text`, which was read from `file`, as `check` reports it for that file. The
/// text read is the text checked.
fn check_text(text: &str, file: &Path) -> Result<Report, String> {
    let scratch = tempfile::tempdir().map_err(|error| {
        format!(
            "Cannot make a folder to check {} in: {error}",
            file.display()
        )
    })?;
    let checked = scratch.path().join("catalog.json");
    fs::write(&checked, text)
        .map_err(|error| format!("Cannot write {}: {error}", checked.display()))?;
    let mut report = check_target(&Target::File(checked.clone()))?;
    report.text = report
        .text
        .replace(&checked.display().to_string(), &file.display().to_string());
    Ok(report)
}

fn user_file(info: &CatalogInfo) -> Option<&String> {
    match &info.id {
        CatalogId::User(file) => Some(file),
        CatalogId::BuiltIn(_) => None,
    }
}

/// Whether both paths are the same directory entry, however they are spelled; links are not followed.
#[cfg(unix)]
fn same_file(a: &Path, b: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (fs::symlink_metadata(a), fs::symlink_metadata(b)) {
        (Ok(a), Ok(b)) => (a.dev(), a.ino()) == (b.dev(), b.ino()),
        _ => false,
    }
}

/// Whether both paths are the same file, by their canonical form, which a case-insensitive disk spells as stored.
#[cfg(not(unix))]
fn same_file(a: &Path, b: &Path) -> bool {
    matches!((fs::canonicalize(a), fs::canonicalize(b)), (Ok(a), Ok(b)) if a == b)
}
