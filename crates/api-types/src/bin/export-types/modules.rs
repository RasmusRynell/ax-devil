//! The registry of exported types, the check that no two share a name, and the per-module TypeScript files.

use std::any::TypeId;
use std::collections::{BTreeMap, BTreeSet};
use ts_rs::{TS, TypeVisitor};

/// A constant emitted at the end of its module's file.
pub struct Constant {
    /// The Rust module whose file holds it.
    pub module: &'static str,
    /// Its TypeScript name.
    pub name: String,
    /// Its declaration.
    pub text: String,
}

struct Registered {
    id: TypeId,
    module: String,
    rust: String,
    ts: String,
    declaration: String,
    dependencies: Vec<(TypeId, String)>,
}

/// Every type reached from the roots, under the module that defines it.
#[derive(Default)]
pub struct Registry {
    types: Vec<Registered>,
    problems: Vec<String>,
}

impl TypeVisitor for Registry {
    fn visit<T: TS + 'static + ?Sized>(&mut self) {
        let id = TypeId::of::<T>();
        if T::output_path().is_none() || self.types.iter().any(|registered| registered.id == id) {
            return;
        }
        let full = std::any::type_name::<T>();
        let path = full.split('<').next().unwrap_or(full);
        let (module, rust) = path.rsplit_once("::").unwrap_or(("", path));
        match T::export_to_string() {
            Ok(exported) => self.types.push(Registered {
                id,
                module: module.to_owned(),
                rust: rust.to_owned(),
                ts: T::ident(),
                declaration: crate::declaration(exported),
                dependencies: T::dependencies()
                    .into_iter()
                    .filter(|dependency| dependency.type_id != id)
                    .map(|dependency| (dependency.type_id, dependency.ts_name))
                    .collect(),
            }),
            Err(error) => self
                .problems
                .push(format!("{full} cannot be exported: {error}")),
        }
        T::visit_dependencies(self);
    }
}

impl Registry {
    /// Everything wrong with the registry: types that cannot be exported and TypeScript names that two types or
    /// constants share.
    pub fn problems(&self, constants: &[Constant]) -> Vec<String> {
        let mut problems = self.problems.clone();
        let mut names: BTreeMap<&str, Vec<String>> = BTreeMap::new();
        for t in &self.types {
            names
                .entry(&t.ts)
                .or_default()
                .push(format!("{}::{}", t.module, t.rust));
        }
        for c in constants {
            names
                .entry(&c.name)
                .or_default()
                .push(format!("{}::{}", c.module, c.name));
        }
        for (name, owners) in names.iter().filter(|(_, owners)| owners.len() > 1) {
            problems.push(format!(
                "{} types export as {name}: {}",
                owners.len(),
                owners.join(", ")
            ));
        }
        problems
    }

    /// The file for every module, keyed by its stem (the module path with `.` for `::`).
    pub fn files(&self, constants: &[Constant]) -> BTreeMap<String, String> {
        let stems: BTreeMap<TypeId, String> =
            self.types.iter().map(|t| (t.id, stem(&t.module))).collect();
        let mut modules: BTreeSet<&str> = self.types.iter().map(|t| t.module.as_str()).collect();
        modules.extend(constants.iter().map(|c| c.module));
        modules
            .into_iter()
            .map(|module| {
                let own = stem(module);
                let mut types: Vec<&Registered> =
                    self.types.iter().filter(|t| t.module == module).collect();
                types.sort_by(|a, b| a.ts.cmp(&b.ts));
                let mut imports: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
                for t in &types {
                    for (id, name) in &t.dependencies {
                        if let Some(from) = stems.get(id).filter(|from| **from != own) {
                            imports.entry(from).or_default().insert(name);
                        }
                    }
                }
                let mut declarations: Vec<&str> =
                    types.iter().map(|t| t.declaration.as_str()).collect();
                declarations.extend(
                    constants
                        .iter()
                        .filter(|c| c.module == module)
                        .map(|c| c.text.as_str()),
                );
                let body = declarations.join("\n\n");
                let mut head = format!("// Generated from {module} by make; do not edit.\n\n");
                let settings: Vec<&str> = ["CommandId", "SettingKey"]
                    .into_iter()
                    .filter(|word| contains_word(&body, word))
                    .collect();
                let mut lines: Vec<String> = imports
                    .iter()
                    .map(|(from, names)| {
                        format!("import type {{ {} }} from \"./{from}\";", join(names))
                    })
                    .collect();
                if !settings.is_empty() {
                    lines.push(format!(
                        "import type {{ {} }} from \"../settings\";",
                        settings.join(", ")
                    ));
                }
                for line in &lines {
                    head.push_str(line);
                    head.push('\n');
                }
                if !lines.is_empty() {
                    head.push('\n');
                }
                (own, format!("{head}{body}\n"))
            })
            .collect()
    }
}

/// The entry file, re-exporting every module file.
pub fn entry(stems: impl Iterator<Item = impl AsRef<str>>) -> String {
    let mut text = String::from("// Generated from the Rust API types by make; do not edit.\n\n");
    for stem in stems {
        text.push_str(&format!("export * from \"./types/{}\";\n", stem.as_ref()));
    }
    text
}

fn stem(module: &str) -> String {
    module.replace("::", ".")
}

fn join(names: &BTreeSet<&str>) -> String {
    names.iter().copied().collect::<Vec<_>>().join(", ")
}

/// Whether `word` occurs in `text` as a whole identifier.
fn contains_word(text: &str, word: &str) -> bool {
    let is_word = |c: char| c.is_alphanumeric() || c == '_';
    text.match_indices(word).any(|(start, _)| {
        !text[..start].chars().next_back().is_some_and(is_word)
            && !text[start + word.len()..]
                .chars()
                .next()
                .is_some_and(is_word)
    })
}
