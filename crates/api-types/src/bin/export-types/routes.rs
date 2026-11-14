//! The JSON route table as `frontend/src/generated/routes.ts`: each route's request, response and path parameters,
//! keyed by `"METHOD /path"`, which is what `ask` takes.

use api_types::endpoints::{ENDPOINTS, visit_types};
use std::collections::BTreeSet;
use ts_rs::{TS, TypeVisitor};

/// Collects the names of the exported types the routes mention, for the import line.
#[derive(Default)]
struct Names(BTreeSet<String>);

impl TypeVisitor for Names {
    fn visit<T: TS + 'static + ?Sized>(&mut self) {
        self.0.insert(T::ident());
    }
}

/// `{ id: string; ... }` for the `{id}` segments of `path`, or `null` when it has none.
fn params(path: &str) -> String {
    let names: Vec<String> = path
        .split('/')
        .filter_map(|part| part.strip_prefix('{')?.strip_suffix('}'))
        .map(|name| format!("{name}: string"))
        .collect();
    if names.is_empty() {
        "null".to_owned()
    } else {
        format!("{{ {} }}", names.join("; "))
    }
}

/// The text of `routes.ts`.
pub fn module() -> String {
    let mut names = Names::default();
    visit_types(&mut names);
    let imports = names.0.into_iter().collect::<Vec<_>>().join(", ");
    let rows: String = ENDPOINTS
        .iter()
        .map(|row| {
            format!(
                "  \"{} {}\": {{ request: {}; response: {}; params: {} }};\n",
                row.method.name(),
                row.path,
                (row.request)(),
                (row.response)(),
                params(row.path)
            )
        })
        .collect();
    format!(
        "// Generated from crates/api-types by make; do not edit.\n\n\
         import type {{ {imports} }} from \"./api\";\n\n\
         /** Every JSON route by `\"METHOD /path\"`: what it takes (a GET's request is its query), what it answers (`null` for a 204) and its path parameters. */\n\
         export interface Routes {{\n{rows}}}\n"
    )
}
