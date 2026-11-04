//! Copying a catalog into the user's folder: the new document, its file name, and the write that never overwrites.

use std::{
    fmt,
    fs::{self, OpenOptions},
    io::{self, Write as _},
    path::{Path, PathBuf},
    process,
    sync::atomic::{AtomicU64, Ordering},
};

use foundation::{ErrorCode, UserError};
use serde::{
    Deserialize, Deserializer,
    de::{Error as _, MapAccess, Visitor},
};
use serde_json::{Value, value::RawValue};

use crate::MAX_CATALOG_BYTES;

/// The most file names tried for one copy (`name.json`, `name-2.json`, ...).
const MAX_NAMES: usize = 1000;

/// The most bytes of a file name stem, so `<stem>-1000.json` and its temporary file stay within the 255-byte file name
/// limit of common filesystems.
const MAX_STEM_BYTES: usize = 100;

/// A file name stem for `name`: lowercase, every run of characters outside `a-z 0-9 . _ -` one `-`, none of `- . _`
/// at the ends, at most `MAX_STEM_BYTES` bytes; `catalog` when nothing is left.
pub fn slug(name: &str) -> String {
    let mut slug = String::new();
    let mut gap = false;
    for c in name.trim().to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-') {
            if gap {
                slug.push('-');
                gap = false;
            }
            slug.push(c);
        } else {
            gap = true;
        }
    }
    if gap {
        slug.push('-');
    }
    // The slug is ASCII, so cutting it at a byte count cuts between characters.
    let mut slug = slug.trim_matches(['-', '.', '_']).to_owned();
    slug.truncate(MAX_STEM_BYTES);
    let slug = slug.trim_matches(['-', '.', '_']);
    if slug.is_empty() {
        "catalog".into()
    } else {
        slug.into()
    }
}

/// A document's top-level members in file order, each value as its own text.
pub(crate) struct Members<'a>(pub(crate) Vec<(String, &'a RawValue)>);

impl<'de> Deserialize<'de> for Members<'de> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Reader;

        impl<'de> Visitor<'de> for Reader {
            type Value = Members<'de>;

            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Members<'de>, A::Error> {
                let mut members: Vec<(String, &RawValue)> = Vec::new();
                while let Some(key) = map.next_key::<String>()? {
                    if members.iter().any(|(seen, _)| *seen == key) {
                        return Err(A::Error::custom(format!("\"{key}\" appears twice")));
                    }
                    members.push((key, map.next_value()?));
                }
                Ok(Members(members))
            }
        }

        deserializer.deserialize_map(Reader)
    }
}

fn invalid(reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::CatalogInvalid, reason)
}

fn text_of(text: &str) -> String {
    Value::from(text).to_string()
}

/// The document of a copy of `source_text`, called `source_name`: its members in their order with their text as it
/// was, except `name` (`name`, trimmed) and `description` (which says what the copy is based on). A built-in source
/// passes its id as `origin`, which becomes the copy's `origin` member right after `description`; a user source keeps
/// the `origin` it has.
pub(crate) fn copy_of(
    source_text: &str,
    source_name: &str,
    name: &str,
    origin: Option<&str>,
) -> Result<String, UserError> {
    let name = name.trim();
    if name.is_empty() {
        return Err(invalid("Enter a name for the copy"));
    }
    let Members(members) = serde_json::from_str(source_text)
        .map_err(|error| invalid(format!("Cannot copy: {error}")))?;
    for required in ["name", "description"] {
        if !members.iter().any(|(key, _)| key == required) {
            return Err(invalid(format!(
                "Cannot copy: the catalog has no {required}"
            )));
        }
    }
    let mut lines = Vec::with_capacity(members.len() + 1);
    for (key, raw) in &members {
        let line = |value: &str| format!("  {}: {value}", text_of(key));
        match (key.as_str(), origin) {
            ("name", _) => lines.push(line(&text_of(name))),
            ("description", _) => {
                lines.push(line(&text_of(&format!("Based on {source_name}."))));
                if let Some(origin) = origin {
                    lines.push(format!("  \"origin\": {}", text_of(origin)));
                }
            }
            ("origin", Some(_)) => {}
            _ => lines.push(line(raw.get())),
        }
    }
    let copy = format!("{{\n{}\n}}\n", lines.join(",\n"));
    if copy.len() as u64 > MAX_CATALOG_BYTES {
        return Err(invalid(format!(
            "The copy would be over {MAX_CATALOG_BYTES} bytes, the most a catalog may have"
        )));
    }
    Ok(copy)
}

/// Writes `text` to a new file `<slug>.json` in `folder`, else `<slug>-2.json` and on, creating the folder if need be;
/// the file name. No existing entry is replaced and only files this call created are ever written or removed: the text
/// goes to a uniquely named dot-prefixed temporary file (which the folder poll ignores), which is then hard-linked under
/// the first free name, so no reader ever sees half a catalog and a name taken meanwhile is skipped, not overwritten.
/// A filesystem without hard links gets an exclusive create of the name and the text written into that file instead.
pub(crate) fn write_new(folder: &Path, slug: &str, text: &str) -> Result<String, UserError> {
    let failed = |error: io::Error| {
        UserError::new(
            ErrorCode::SaveFailed,
            format!("Cannot save the copy: {error}"),
        )
    };
    fs::create_dir_all(folder).map_err(failed)?;
    let temporary = write_temporary(folder, slug, text).map_err(failed)?;
    let published = publish(folder, slug, text, &temporary);
    // Ours alone; after a successful link the published name is a second link to the same data.
    let _ = fs::remove_file(&temporary);
    match published {
        Ok(Some(file)) => Ok(file),
        Ok(None) => Err(UserError::new(
            ErrorCode::SaveFailed,
            format!("Cannot save the copy: {MAX_NAMES} catalogs are already named like it"),
        )),
        Err(error) => Err(failed(error)),
    }
}

/// Links `temporary` under the first free `<slug>[-n].json`; `None` when every name is taken.
fn publish(folder: &Path, slug: &str, text: &str, temporary: &Path) -> io::Result<Option<String>> {
    let mut linking = true;
    for number in 1..=MAX_NAMES {
        let file = if number == 1 {
            format!("{slug}.json")
        } else {
            format!("{slug}-{number}.json")
        };
        let target = folder.join(&file);
        if linking {
            // Fails with `AlreadyExists` on any entry of that name, a dangling link included, and replaces nothing.
            match fs::hard_link(temporary, &target) {
                Ok(()) => return Ok(Some(file)),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) if no_hard_links(&error) => linking = false,
                Err(error) => return Err(error),
            }
        }
        // An exclusive create fails on any entry of that name, including a link, and never follows one.
        let mut created = match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
        {
            Ok(created) => created,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        };
        let written = created
            .write_all(text.as_bytes())
            .and_then(|()| created.sync_all());
        return match written {
            Ok(()) => Ok(Some(file)),
            Err(error) => {
                drop(created);
                let _ = fs::remove_file(&target);
                Err(error)
            }
        };
    }
    Ok(None)
}

/// Whether linking failed because the filesystem has no hard links (FAT, some network shares), not because of the
/// name.
fn no_hard_links(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::Unsupported | io::ErrorKind::PermissionDenied
    )
}

/// Creates a temporary file `.<slug>.<pid>-<n>.tmp` in `folder` with `text`, synced; its path. The exclusive create
/// makes the name ours, and a failed write removes it.
fn write_temporary(folder: &Path, slug: &str, text: &str) -> io::Result<PathBuf> {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    loop {
        let number = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = folder.join(format!(".{slug}.{}-{number}.tmp", process::id()));
        let mut file = match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        };
        let written = file
            .write_all(text.as_bytes())
            .and_then(|()| file.sync_all());
        return match written {
            Ok(()) => Ok(path),
            Err(error) => {
                drop(file);
                let _ = fs::remove_file(&path);
                Err(error)
            }
        };
    }
}
