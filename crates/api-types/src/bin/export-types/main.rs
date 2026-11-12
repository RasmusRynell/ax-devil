//! Writes the Rust API types and the playback constants to one file per Rust module under
//! frontend/src/generated/types/ (re-exported by frontend/src/generated/api.ts), the settings and command
//! tables to frontend/src/generated/settings.ts, and the API and shell versions and the reserved shortcuts to
//! desktop/generated/api.json (untracked; the API version is a hash of the TypeScript files), only when they changed.

#[path = "../../../contract_version.rs"]
mod contract_version;
mod modules;
mod scan;

use contract_version::contract_version;
use modules::{Constant, Registry, entry};
use std::collections::BTreeSet;

/// The problems found, shown as plain text rather than a quoted string.
struct Failed(String);

impl std::fmt::Display for Failed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::fmt::Debug for Failed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Failed {}

const TYPES_FOLDER: &str = "frontend/src/generated/types";

/// The entry file, the settings tables and the desktop JSON, in the order they are written.
const FIXED_OUTPUTS: [&str; 3] = [
    "frontend/src/generated/api.ts",
    "frontend/src/generated/settings.ts",
    "desktop/generated/api.json",
];

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut registry = Registry::default();
    api_types::visit_api_types(&mut registry);
    export::visit_api_types(&mut registry);
    foundation::visit_api_types(&mut registry);
    playback::visit_api_types(&mut registry);
    scene::visit_api_types(&mut registry);
    scene_store::visit_api_types(&mut registry);
    sessions::visit_api_types(&mut registry);
    workspace::visit_api_types(&mut registry);
    let constants = constants();
    let found = scan::scan(std::path::Path::new(&repo_path("crates"))).map_err(Failed)?;
    let problems = registry.problems(&found, &constants);
    if !problems.is_empty() {
        return Err(Failed(problems.join("\n")).into());
    }
    let files = registry.files(&constants);
    let mut destinations: Vec<String> = files
        .keys()
        .map(|stem| format!("{TYPES_FOLDER}/{stem}.ts"))
        .collect();
    destinations.extend(FIXED_OUTPUTS.map(str::to_owned));
    check_outputs(&destinations)?;
    let entry = entry(files.keys());
    let settings = settings_module()?;
    for (stem, text) in &files {
        write_if_changed(&format!("{TYPES_FOLDER}/{stem}.ts"), text)?;
    }
    write_if_changed(FIXED_OUTPUTS[0], &entry)?;
    write_if_changed(FIXED_OUTPUTS[1], &settings)?;
    prune(&files.keys().map(|stem| format!("{stem}.ts")).collect())?;
    // The desktop shell and the development supervisor are CommonJS and read these from JSON. The version is
    // hashed from the files just written, as the backend hashes them when it is built.
    let desktop = serde_json::json!({
        "apiVersion": contract_version(std::path::Path::new(&repo_path("frontend/src/generated")))?,
        "shellApi": api_types::SHELL_API,
        "reserved": settings::shortcuts::RESERVED,
    });
    write_if_changed(FIXED_OUTPUTS[2], &format!("{desktop:#}\n"))?;
    Ok(())
}

/// Fails before any write unless every output folder resolves inside the repository (no link anywhere in its path),
/// no destination file is a link, and the types folder has no sub-directory, so no write or deletion can leave the
/// repository or remove a folder.
fn check_outputs(destinations: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let root = std::fs::canonicalize(repo_path(""))?;
    for folder in [TYPES_FOLDER, "frontend/src/generated", "desktop/generated"] {
        let mut relative = std::path::Path::new(folder);
        // The nearest existing ancestor stands in for a folder a fresh checkout does not have yet.
        let outside = loop {
            let full = root.join(relative);
            if std::fs::symlink_metadata(&full).is_ok() {
                break std::fs::canonicalize(&full).map_or(true, |real| real != full);
            }
            match relative.parent() {
                Some(parent) if !parent.as_os_str().is_empty() => relative = parent,
                _ => break false,
            }
        };
        if outside {
            return Err(Failed(format!(
                "{folder} resolves outside the repository (a symlink in its path); remove the link and run make again"
            ))
            .into());
        }
    }
    for path in destinations {
        if std::fs::symlink_metadata(repo_path(path)).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(Failed(format!("{path} is a link; remove it and run make again")).into());
        }
    }
    let Ok(entries) = std::fs::read_dir(repo_path(TYPES_FOLDER)) else {
        return Ok(());
    };
    for entry in entries {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            return Err(Failed(format!(
                "{TYPES_FOLDER}/{} is a directory; remove it and run make again",
                entry.file_name().to_string_lossy()
            ))
            .into());
        }
    }
    Ok(())
}

/// Deletes the files an earlier run generated in the types folder and this run did not. A link is removed as itself.
fn prune(current: &BTreeSet<String>) -> std::io::Result<()> {
    let Ok(entries) = std::fs::read_dir(repo_path(TYPES_FOLDER)) else {
        return Ok(());
    };
    for entry in entries {
        let entry = entry?;
        if !entry
            .file_name()
            .to_str()
            .is_some_and(|name| current.contains(name))
        {
            std::fs::remove_file(entry.path())?;
        }
    }
    Ok(())
}

/// The setting table, the commands and the reserved keys as TypeScript data, with the key unions derived from them.
fn settings_module() -> Result<String, serde_json::Error> {
    use settings::shortcuts::{COMMANDS, RESERVED};
    Ok(format!(
        "// Generated from crates/settings by make; do not edit.\n\n\
         {}\n\n{}\n\n{}\n\n\
         /** The dotted key of a setting. */\n\
         export type SettingKey = (typeof SETTINGS)[number][\"key\"];\n\n\
         /** The id of a command. */\n\
         export type CommandId = (typeof COMMANDS)[number][\"id\"];\n",
        data(
            "SETTINGS",
            "Every setting, in the order the settings dialog shows them.",
            &settings::TABLE
        )?,
        data(
            "COMMANDS",
            "Every command with its default key, in menu order.",
            &COMMANDS
        )?,
        data(
            "RESERVED",
            "Keys the desktop shell keeps for page zoom; no command may use them.",
            &RESERVED
        )?,
    ))
}

/// `value` as a TypeScript constant with a literal type, under a doc comment.
fn data(name: &str, doc: &str, value: &impl serde::Serialize) -> Result<String, serde_json::Error> {
    Ok(format!(
        "/** {doc} */\nexport const {name} = {} as const;",
        serde_json::to_string_pretty(value)?
    ))
}

/// The playback and render constants as TypeScript consts, each under its doc comment, with the module whose file
/// holds them.
fn constants() -> Vec<Constant> {
    [
        ("api_types::playback", api_types::playback::CONSTANTS),
        ("api_types::render", api_types::render::CONSTANTS),
    ]
    .into_iter()
    .flat_map(|(module, table)| {
        table.iter().map(move |(name, value, doc)| Constant {
            module,
            name: (*name).to_owned(),
            text: format!(
                "/**\n *{}\n */\nexport const {name} = {value};",
                doc.trim_end().replace('\n', "\n *")
            ),
        })
    })
    .collect()
}

/// Drops what ts-rs puts above every type: the generator comment and the per-type imports, which each module file
/// writes once itself.
fn declaration(exported: String) -> String {
    exported
        .lines()
        .filter(|line| !line.starts_with("import type "))
        .skip_while(|line| line.starts_with("//") || line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

/// `path`, which is relative to the repository root, as a path from this crate.
fn repo_path(path: &str) -> String {
    format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"))
}

fn write_if_changed(path: &str, text: &str) -> std::io::Result<()> {
    let destination = repo_path(path);
    if std::fs::read_to_string(&destination).ok().as_deref() == Some(text) {
        return Ok(());
    }
    if let Some(folder) = std::path::Path::new(&destination).parent() {
        std::fs::create_dir_all(folder)?;
    }
    std::fs::write(destination, text)
}
