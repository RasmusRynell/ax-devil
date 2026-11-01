//! The catalog compiler: from catalog text to the [`ir`](crate::ir) the evaluator runs, naming every problem found.
//!
//! The document is read and its limits checked ([`document`](crate::document)); templates are declared, then each
//! is compiled once for any inputs (so cycles and problems in uncalled templates are found) and once more per set
//! of constant call-site inputs; recipes are compiled last and routed. Compilation goes on past a problem wherever
//! the rest of the catalog can still be checked, so one pass reports every independent problem.

mod call;
mod coerce;
mod expr;
mod fold;
mod program;
mod recipes;
mod scene;
mod steps;
mod templates;

use expr::Shared;
use templates::CatalogCompiler;

use crate::diagnostics::{Pointer, Problem, Problems, codes};
use crate::document::{self, Json};
use crate::ir::Catalog;
use crate::validate::structure::{keys, object};
use crate::{CatalogDiagnostic, CatalogMetadata, SCHEMA_VERSION};

/// Compiles the catalog `source`, or names every problem in it.
pub(crate) fn catalog(source: &str) -> Result<(CatalogMetadata, Catalog), Vec<CatalogDiagnostic>> {
    let mut problems = Problems::default();
    let Some(document) = document::parse(source, &mut problems) else {
        return Err(problems.into_vec());
    };
    let root = Pointer::root();
    let top = match object(&document, &root) {
        Ok(top) => top,
        Err(problem) => {
            problems.push(problem);
            return Err(problems.into_vec());
        }
    };
    let metadata = metadata(top, &mut problems);
    if let Err(problem) = keys(
        top,
        &["metadata", "templates", "recipes"],
        &["metadata", "templates", "recipes"],
        &root,
    ) {
        problems.push(problem);
    }
    let get = |key: &str| {
        top.iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    };
    let (templates_at, recipes_at) = (root.key("templates"), root.key("recipes"));
    let templates = get("templates").map(|templates| object(templates, &templates_at));
    let recipes = get("recipes").map(|recipes| object(recipes, &recipes_at));
    let (Some(templates), Some(recipes)) = (templates, recipes) else {
        return Err(problems.into_vec());
    };
    let (templates, recipes) = match (templates, recipes) {
        (Ok(templates), Ok(recipes)) => (templates, recipes),
        (templates, recipes) => {
            problems.extend([templates.err(), recipes.err()].into_iter().flatten());
            return Err(problems.into_vec());
        }
    };
    let mut compiler = CatalogCompiler::new(
        Shared {
            problems,
            ..Shared::default()
        },
        templates,
        &templates_at,
    );
    compiler.compile_all();
    let compiled = compiler.recipes(recipes, &recipes_at);
    let Shared {
        locations,
        problems,
    } = compiler.shared;
    match metadata {
        Some(metadata) if problems.is_empty() => Ok((
            metadata,
            Catalog {
                recipes: compiled.recipes.into(),
                routing: compiled.routing,
                features: compiled.features,
                locations: locations.into_list(),
            },
        )),
        _ => Err(problems.into_vec()),
    }
}

/// Reads and checks `metadata`, recording each problem.
fn metadata(top: &[(String, Json)], problems: &mut Problems) -> Option<CatalogMetadata> {
    let at = Pointer::root().key("metadata");
    let raw = top
        .iter()
        .find(|(key, _)| key == "metadata")
        .map_or(&Json::Null, |(_, value)| value);
    let metadata = match object(raw, &at) {
        Ok(metadata) => metadata,
        Err(problem) => {
            problems.push(problem);
            return None;
        }
    };
    let allowed = [
        "id",
        "name",
        "schema_version",
        "description",
        "design_notes",
        "origin",
    ];
    let get = |key: &str| {
        metadata
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    };
    let checks = [
        keys(metadata, &allowed, &["id", "name", "schema_version"], &at),
        match get("schema_version") {
            None | Some(Json::Int(3)) => Ok(()),
            Some(_) => {
                let message = format!(
                    "Unsupported Scene Render Catalog schema version; expected {SCHEMA_VERSION}."
                );
                Err(Problem::new(codes::UNSUPPORTED_VERSION, message).at(&at.key("schema_version")))
            }
        },
        match get("description") {
            None | Some(Json::Str(_)) => Ok(()),
            Some(_) => {
                Err(Problem::new(codes::INVALID_STRUCTURE, "Expected text.")
                    .at(&at.key("description")))
            }
        },
        match get("design_notes") {
            None | Some(Json::Object(_)) => Ok(()),
            Some(_) => Err(
                Problem::new(codes::INVALID_STRUCTURE, "Expected an object.")
                    .at(&at.key("design_notes")),
            ),
        },
    ];
    let mut valid = true;
    for problem in checks.into_iter().filter_map(Result::err) {
        problems.push(problem);
        valid = false;
    }
    let mut text = |key: &str| match get(key)
        .and_then(Json::as_str)
        .filter(|text| !text.is_empty())
    {
        Some(text) => Some(text.to_string()),
        None => {
            if get(key).is_some() {
                problems.push(Problem::value("Expected nonempty text.").at(&at.key(key)));
            }
            None
        }
    };
    let (id, name) = (text("id"), text("name"));
    let origin = origin(get("origin"), &at.key("origin"), problems);
    let description = get("description")
        .and_then(Json::as_str)
        .unwrap_or_default()
        .to_string();
    match (id, name, origin) {
        (Some(id), Some(name), Ok(origin)) if valid => Some(CatalogMetadata {
            id,
            name,
            description,
            origin,
        }),
        _ => None,
    }
}

/// The catalog an `origin` names: `{"based_on": <nonempty text>, "base_hash": <text>}`.
fn origin(raw: Option<&Json>, at: &Pointer, problems: &mut Problems) -> Result<Option<String>, ()> {
    let Some(raw) = raw else { return Ok(None) };
    let checked = object(raw, at).and_then(|origin| {
        keys(
            origin,
            &["based_on", "base_hash"],
            &["based_on", "base_hash"],
            at,
        )?;
        if raw.get("base_hash").and_then(Json::as_str).is_none() {
            return Err(
                Problem::new(codes::INVALID_STRUCTURE, "Expected text.").at(&at.key("base_hash"))
            );
        }
        let based_on = raw
            .get("based_on")
            .and_then(Json::as_str)
            .filter(|text| !text.is_empty());
        based_on
            .map(str::to_string)
            .ok_or_else(|| Problem::value("Expected nonempty text.").at(&at.key("based_on")))
    });
    checked.map(Some).map_err(|problem| problems.push(problem))
}
