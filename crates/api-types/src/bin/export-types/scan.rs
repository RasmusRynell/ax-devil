//! Finds every type in the workspace's crates that derives `TS`, so a type nobody registered cannot go unnoticed. Types
//! produced by macros (including `include!`) are not seen; the dependency check still catches those that an exported
//! type uses. A `TS` type inside a function or block, a renamed `TS` import and a `#[path]` module fail the scan.

use std::path::Path;
use syn::ext::IdentExt;
use syn::punctuated::Punctuated;
use syn::visit::{self, Visit};
use syn::{Attribute, Meta, Path as SynPath, Token};

/// A type found deriving `TS` in a source file.
pub struct Found {
    /// The file, relative to the repository root.
    pub file: String,
    /// The Rust module that defines the type, as in `foundation::time`.
    pub module: String,
    /// The type's Rust name.
    pub name: String,
    /// Whether the derive sits inside a `cfg_attr`, so it applies only when that condition holds.
    pub conditional: bool,
}

/// Every module-level `TS`-deriving struct, enum and union under `crates/*/src`, except in `src/bin`.
pub fn scan(crates: &Path) -> Result<Vec<Found>, String> {
    let mut found = Vec::new();
    let mut crate_dirs = read_dir(crates)?;
    crate_dirs.sort();
    for dir in crate_dirs {
        let Some(name) = dir.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        let mut files = Vec::new();
        collect(&dir.join("src"), &mut files)?;
        files.sort();
        for file in files {
            let Ok(relative) = file.strip_prefix(dir.join("src")) else {
                continue;
            };
            let module = module_of(name, &relative.to_string_lossy().replace('\\', "/"));
            let text = std::fs::read_to_string(&file)
                .map_err(|error| format!("cannot read {}: {error}", file.display()))?;
            let file = format!(
                "crates/{name}/src/{}",
                relative.to_string_lossy().replace('\\', "/")
            );
            for (module, name, conditional) in declared(&text, &module, &file)? {
                found.push(Found {
                    file: file.clone(),
                    module,
                    name,
                    conditional,
                });
            }
        }
    }
    if found.is_empty() {
        return Err(format!(
            "no TS types found under {}; run the exporter from the repository",
            crates.display()
        ));
    }
    Ok(found)
}

fn read_dir(dir: &Path) -> Result<Vec<std::path::PathBuf>, String> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Ok(Vec::new());
    };
    entries
        .map(|entry| {
            entry
                .map(|entry| entry.path())
                .map_err(|error| format!("cannot read {}: {error}", dir.display()))
        })
        .collect()
}

/// The `.rs` files under `dir`, leaving out a `bin` folder directly under `src`.
fn collect(dir: &Path, files: &mut Vec<std::path::PathBuf>) -> Result<(), String> {
    for path in read_dir(dir)? {
        if path.is_dir() {
            if !(path.file_name().is_some_and(|name| name == "bin") && dir.ends_with("src")) {
                collect(&path, files)?;
            }
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            files.push(path);
        }
    }
    Ok(())
}

/// The Rust module path of `relative` (a path under the crate's `src`) in the crate directory `crate_dir`.
fn module_of(crate_dir: &str, relative: &str) -> String {
    let stem = relative.strip_suffix(".rs").unwrap_or(relative);
    let stem = stem.strip_suffix("/mod").unwrap_or(stem);
    let mut path = vec![crate_dir.replace('-', "_")];
    if stem != "lib" {
        path.extend(stem.split('/').map(str::to_owned));
    }
    path.join("::")
}

/// The `(module, name, conditional)` of every struct, enum and union in `source`, which is the file `file` of module
/// `module`, that derives `TS`. Inline modules extend the path; `#[cfg(test)]` items are left out. A `TS` type
/// inside a function or block, or an import that renames `TS`, is an error: the scan could not tell what it sees.
fn declared(source: &str, module: &str, file: &str) -> Result<Vec<(String, String, bool)>, String> {
    let parsed =
        syn::parse_file(source).map_err(|error| format!("{file}: cannot parse: {error}"))?;
    let mut walk = Walk {
        file,
        module: module.to_owned(),
        depth: 0,
        found: Vec::new(),
        errors: Vec::new(),
    };
    walk.visit_file(&parsed);
    if walk.errors.is_empty() {
        Ok(walk.found)
    } else {
        Err(walk.errors.join("\n"))
    }
}

struct Walk<'a> {
    file: &'a str,
    module: String,
    /// How many blocks enclose the item being visited; module level is 0.
    depth: usize,
    found: Vec<(String, String, bool)>,
    errors: Vec<String>,
}

impl Walk<'_> {
    fn item(&mut self, attrs: &[Attribute], name: &syn::Ident) {
        let Some(attr) = attrs.iter().find(|attr| applies(&attr.meta, &is_derive_ts)) else {
            return;
        };
        let name = name.unraw();
        if self.depth > 0 {
            self.errors.push(format!(
                "{}: {name} derives TS inside a function or block; declare wire types at module level so the export check can see them.",
                self.file
            ));
        } else {
            self.found.push((
                self.module.clone(),
                name.to_string(),
                attr.path().is_ident("cfg_attr"),
            ));
        }
    }
}

/// Visits the node unless it carries `#[cfg(test)]`.
macro_rules! skip_tests {
    ($($method:ident($node:ident, $visit:path)),* $(,)?) => {$(
        fn $method(&mut self, node: &'ast syn::$node) {
            if !is_test(&node.attrs) {
                $visit(self, node);
            }
        }
    )*};
}

impl<'ast> Visit<'ast> for Walk<'_> {
    skip_tests!(
        visit_item_fn(ItemFn, visit::visit_item_fn),
        visit_item_impl(ItemImpl, visit::visit_item_impl),
        visit_item_const(ItemConst, visit::visit_item_const),
        visit_item_static(ItemStatic, visit::visit_item_static),
        visit_item_trait(ItemTrait, visit::visit_item_trait),
        visit_impl_item_fn(ImplItemFn, visit::visit_impl_item_fn),
    );

    fn visit_item_mod(&mut self, node: &'ast syn::ItemMod) {
        if is_test(&node.attrs) {
            return;
        }
        let name = node.ident.unraw();
        if node.attrs.iter().any(|attr| applies(&attr.meta, &is_path)) {
            self.errors.push(format!(
                "{}: module {name} uses #[path]; the export check maps files to modules by their location, so declare the module at its default path.",
                self.file
            ));
            return;
        }
        let inner = format!("{}::{name}", self.module);
        let outer = std::mem::replace(&mut self.module, inner);
        visit::visit_item_mod(self, node);
        self.module = outer;
    }

    fn visit_block(&mut self, node: &'ast syn::Block) {
        self.depth += 1;
        visit::visit_block(self, node);
        self.depth -= 1;
    }

    // The traversal continues into the definition: a discriminant or array length can hold a block.
    fn visit_item_struct(&mut self, node: &'ast syn::ItemStruct) {
        if !is_test(&node.attrs) {
            self.item(&node.attrs, &node.ident);
            visit::visit_item_struct(self, node);
        }
    }

    fn visit_item_enum(&mut self, node: &'ast syn::ItemEnum) {
        if !is_test(&node.attrs) {
            self.item(&node.attrs, &node.ident);
            visit::visit_item_enum(self, node);
        }
    }

    fn visit_item_union(&mut self, node: &'ast syn::ItemUnion) {
        if !is_test(&node.attrs) {
            self.item(&node.attrs, &node.ident);
            visit::visit_item_union(self, node);
        }
    }

    fn visit_use_rename(&mut self, node: &'ast syn::UseRename) {
        if node.ident == "TS" {
            self.errors.push(format!(
                "{}: imports ts_rs::TS as {}; import it under its own name so the export check can find the types that derive it.",
                self.file, node.rename
            ));
        }
    }
}

fn is_test(attrs: &[Attribute]) -> bool {
    attrs.iter().any(|attr| {
        attr.path().is_ident("cfg")
            && attr
                .parse_args::<syn::Ident>()
                .is_ok_and(|arg| arg == "test")
    })
}

/// Whether `meta`, or an attribute a `cfg_attr` (nested or not) applies, satisfies `direct`.
fn applies(meta: &Meta, direct: &impl Fn(&Meta) -> bool) -> bool {
    if direct(meta) {
        return true;
    }
    let Meta::List(list) = meta else {
        return false;
    };
    list.path.is_ident("cfg_attr")
        && list
            .parse_args_with(Punctuated::<Meta, Token![,]>::parse_terminated)
            .is_ok_and(|metas| metas.iter().skip(1).any(|meta| applies(meta, direct)))
}

fn is_path(meta: &Meta) -> bool {
    meta.path().is_ident("path")
}

/// Whether the attribute is `derive(.., TS, ..)`.
fn is_derive_ts(meta: &Meta) -> bool {
    let Meta::List(list) = meta else {
        return false;
    };
    list.path.is_ident("derive")
        && list
            .parse_args_with(Punctuated::<SynPath, Token![,]>::parse_terminated)
            .is_ok_and(|paths| {
                paths
                    .iter()
                    .any(|path| path.segments.last().is_some_and(|last| last.ident == "TS"))
            })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_ts_types_and_maps_files_to_modules() {
        let source = r#"
#[derive(Debug, Clone)]
pub struct Plain;

#[derive(
    Debug,
    TS,
)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Spanning {}

#[derive(ts_rs::TS)]
enum Qualified {}

#[cfg_attr(all(), derive(ts_rs::TS))]
struct Conditional {}

mod nested {
    #[derive(TS)]
    pub struct Inner;
}

#[cfg(test)]
mod tests {
    #[derive(TS)]
    struct InTests;
}

// #[derive(TS)] struct Commented;
"#;
        let found = declared(source, "outer", "outer.rs").unwrap();
        let found: Vec<_> = found
            .iter()
            .map(|(m, n, c)| (m.as_str(), n.as_str(), *c))
            .collect();
        assert_eq!(
            found,
            [
                ("outer", "Spanning", false),
                ("outer", "Qualified", false),
                ("outer", "Conditional", true),
                ("outer::nested", "Inner", false),
            ]
        );
        let hidden = "#[derive(ts_rs::TS)] struct Hidden;";
        for in_block in [
            format!("fn f() {{ {hidden} }}"),
            format!("enum E {{ A = {{ {hidden} 0 }} }}"),
            format!("struct S {{ f: [u8; {{ {hidden} 0 }}] }}"),
        ] {
            assert_eq!(
                declared(&in_block, "outer", "outer.rs").unwrap_err(),
                "outer.rs: Hidden derives TS inside a function or block; declare wire types at module level so the export check can see them."
            );
        }
        let path_mod = "#[cfg(test)] #[path = \"t.rs\"] mod tests;\n#[path = \"../x.rs\"] mod x;";
        assert_eq!(
            declared(path_mod, "outer", "outer.rs").unwrap_err(),
            "outer.rs: module x uses #[path]; the export check maps files to modules by their location, so declare the module at its default path."
        );
        let raw = "mod r#mod { #[derive(TS)] struct r#type; }";
        assert_eq!(
            declared(raw, "outer", "outer.rs").unwrap(),
            [("outer::mod".to_owned(), "type".to_owned(), false)]
        );
        let renamed = "use ts_rs::TS as Ts;\n#[derive(Ts)]\nstruct Probe;";
        assert_eq!(
            declared(renamed, "outer", "outer.rs").unwrap_err(),
            "outer.rs: imports ts_rs::TS as Ts; import it under its own name so the export check can find the types that derive it."
        );
        assert_eq!(module_of("scene-store", "lib.rs"), "scene_store");
        assert_eq!(module_of("sessions", "session/mod.rs"), "sessions::session");
        assert_eq!(
            module_of("api-types", "playback/lane.rs"),
            "api_types::playback::lane"
        );
    }
}
