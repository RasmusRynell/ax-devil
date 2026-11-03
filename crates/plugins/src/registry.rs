//! `plugins.json`: which plugin manifests the app loads. Written only by install and remove, never by scanning
//! folders.

use std::{
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

use serde_json::{Map, Value, json};

use crate::{
    manifest::{MANIFEST_FILE, Manifest, ManifestError},
    reserved::Reserved,
};

/// The registry file version this build reads and writes.
const VERSION: u64 = 2;

/// The registry file could not be used.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RegistryError(pub String);

impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RegistryError {}

/// The plugins the user installed, in install order. Keys this build does not know are kept when it writes.
#[derive(Clone, Debug)]
pub struct Registry {
    path: PathBuf,
    /// Top-level keys besides `version` and `plugins`.
    other: Map<String, Value>,
    /// One object per plugin; `manifest` is its absolute manifest path.
    entries: Vec<Map<String, Value>>,
}

impl Registry {
    /// Reads the registry at `path`. A missing file is an empty registry.
    pub fn read(path: &Path) -> Result<Self, RegistryError> {
        let unusable = |reason: String| RegistryError(format!("{}: {reason}", path.display()));
        let mut registry = Self {
            path: path.to_path_buf(),
            other: Map::new(),
            entries: Vec::new(),
        };
        let text = match fs::read_to_string(path) {
            Ok(text) => text,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(registry),
            Err(error) => return Err(unusable(error.to_string())),
        };
        let Value::Object(mut fields) = serde_json::from_str(&text)
            .map_err(|error| unusable(format!("not valid JSON: {error}")))?
        else {
            return Err(unusable("must be a JSON object".to_owned()));
        };
        match fields
            .remove("version")
            .and_then(|version| version.as_u64())
        {
            Some(VERSION) => {}
            Some(newer) if newer > VERSION => {
                return Err(unusable(format!(
                    "version {newer} was written by a newer ax-devil"
                )));
            }
            _ => return Err(unusable(format!("version must be {VERSION}"))),
        }
        let Some(Value::Array(plugins)) = fields.remove("plugins") else {
            return Err(unusable("plugins must be a list".to_owned()));
        };
        for plugin in plugins {
            match plugin {
                Value::Object(entry) if entry.get("manifest").is_some_and(Value::is_string) => {
                    registry.entries.push(entry);
                }
                _ => return Err(unusable("every plugin needs a manifest path".to_owned())),
            }
        }
        registry.other = fields;
        Ok(registry)
    }

    /// The manifest paths in install order.
    pub fn manifests(&self) -> Vec<PathBuf> {
        self.entries.iter().map(entry_manifest).collect()
    }

    /// Adds the plugin at `path`, a folder holding `ax-devil-plugin.json` or the manifest itself. Nothing is written
    /// until [`save`](Self::save). `reserved` holds the ids the built-ins take.
    ///
    /// The plugin id and every handler id must be new: an installed plugin that has one, or a reserved one, refuses
    /// it.
    pub fn install(&mut self, path: &Path, reserved: &Reserved) -> Result<Manifest, ManifestError> {
        let (file, manifest) = read_manifest(path)?;
        let mut manifests = self.load_all();
        manifests.push(Ok(manifest.clone()));
        if let Some(Err(error)) = reserved.claim_in_order(manifests).last() {
            return Err(error);
        }
        self.entries.push(entry_of(&file));
        Ok(manifest)
    }

    /// The position of the plugin that `target` names: its manifest or folder path, which always names exactly one,
    /// or the id its manifest declares. `None` when it names no plugin; an error when an id is declared by more
    /// than one (a plugin that fails to load takes no id, so two can end up declaring the same one).
    pub fn find(&self, target: &str) -> Result<Option<usize>, RegistryError> {
        let by_path = self.entries.iter().position(|entry| {
            let manifest = entry_manifest(entry);
            manifest == Path::new(target)
                || manifest
                    .parent()
                    .is_some_and(|folder| folder == Path::new(target))
        });
        if by_path.is_some() {
            return Ok(by_path);
        }
        let mut declaring = self.entries.iter().enumerate().filter(|(_, entry)| {
            Manifest::load(&entry_manifest(entry)).is_ok_and(|loaded| loaded.id == target)
        });
        let first = declaring.next().map(|(index, _)| index);
        match declaring.count() {
            0 => Ok(first),
            more => Err(RegistryError(format!(
                "'{target}' is declared by {} plugins; name its manifest or folder",
                more + 1
            ))),
        }
    }

    /// Replaces the plugin at `index` (from [`find`](Self::find)) by the one at `path`, in the same place in the
    /// order, after reading and validating its manifest again. The whole registry is claimed in load order, as `load`
    /// does, with and without the replacement. It is refused when the new manifest does not load in its place, or
    /// when any plugin that loads today would stop loading.
    pub fn update(
        &mut self,
        index: usize,
        path: &Path,
        reserved: &Reserved,
    ) -> Result<Manifest, ManifestError> {
        let (file, manifest) = read_manifest(path)?;
        let today = self.load_all();
        let mut replaced = today.clone();
        replaced[index] = Ok(manifest.clone());
        let before: Vec<_> = reserved.claim_in_order(today).collect();
        let after: Vec<_> = reserved.claim_in_order(replaced).collect();
        if let Err(error) = &after[index] {
            return Err(error.clone());
        }
        for (before, after) in before.iter().zip(&after) {
            if let (Ok(plugin), Err(error)) = (before, after) {
                return Err(ManifestError::file(format!(
                    "plugin '{}' would stop loading: {}",
                    plugin.id, error.reason
                )));
            }
        }
        self.entries[index] = entry_of(&file);
        Ok(manifest)
    }

    /// Every entry's manifest, read again, in install order.
    fn load_all(&self) -> Vec<Result<Manifest, ManifestError>> {
        self.entries
            .iter()
            .map(|entry| Manifest::load(&entry_manifest(entry)))
            .collect()
    }

    /// Removes the plugin [`find`](Self::find) returns for `target` (the only way to remove one that no longer loads
    /// is its manifest or folder path). Its files are left alone. Returns whether one was removed.
    pub fn remove(&mut self, target: &str) -> Result<bool, RegistryError> {
        let found = self.find(target)?;
        if let Some(index) = found {
            self.entries.remove(index);
        }
        Ok(found.is_some())
    }

    /// Writes the registry atomically: a temporary file beside it, private to the user, renamed into place.
    pub fn save(&self) -> io::Result<()> {
        let folder = self.path.parent().unwrap_or_else(|| Path::new("."));
        create_private_folder(folder)?;
        let mut document = Map::new();
        document.insert("version".to_owned(), json!(VERSION));
        document.insert(
            "plugins".to_owned(),
            Value::Array(self.entries.iter().cloned().map(Value::Object).collect()),
        );
        document.extend(self.other.clone());
        let text =
            serde_json::to_string_pretty(&Value::Object(document)).map_err(io::Error::other)?;

        let mut file = tempfile::Builder::new()
            .prefix(".plugins-")
            .tempfile_in(folder)?;
        file.write_all(text.as_bytes())?;
        file.write_all(b"\n")?;
        file.as_file().sync_all()?;
        // `tempfile` creates the file readable by its owner only.
        file.persist(&self.path).map_err(|error| error.error)?;
        Ok(())
    }
}

/// The manifest file `path` names, canonical, and what it declares.
fn read_manifest(path: &Path) -> Result<(PathBuf, Manifest), ManifestError> {
    let file = if path.is_dir() {
        path.join(MANIFEST_FILE)
    } else {
        path.to_path_buf()
    };
    let file = fs::canonicalize(&file)
        .map_err(|error| ManifestError::file(format!("cannot read {}: {error}", file.display())))?;
    let manifest = Manifest::load(&file)?;
    Ok((file, manifest))
}

fn entry_of(manifest: &Path) -> Map<String, Value> {
    Map::from_iter([(
        "manifest".to_owned(),
        Value::String(manifest.to_string_lossy().into_owned()),
    )])
}

fn entry_manifest(entry: &Map<String, Value>) -> PathBuf {
    PathBuf::from(
        entry
            .get("manifest")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    )
}

fn create_private_folder(folder: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(folder)
}
