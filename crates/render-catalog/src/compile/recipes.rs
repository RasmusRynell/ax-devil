//! Recipes: selectors, bindings, the scopes each recipe reads and the routing table.

use std::collections::HashMap;
use std::sync::Arc;

use super::expr::Fault;
use super::program::Roots;
use super::templates::CatalogCompiler;
use crate::FeatureSet;
use crate::definitions::{Type, types};
use crate::diagnostics::{Pointer, Problem};
use crate::document::Json;
use crate::ir::{Binding, BindingShape, Recipe, Routing, SceneKind, TypeName, ValueType};
use crate::validate::structure::{descriptive, keys, list, object};

/// The recipe groups, in the order recipes are compiled and listed.
const GROUPS: [&str; 3] = ["fallbacks", "classifications", "relations"];

/// The compiled recipes with their routing table and the features they draw.
pub(super) struct Recipes {
    pub(super) recipes: Vec<Recipe>,
    pub(super) routing: Routing,
    pub(super) features: FeatureSet,
}

impl CatalogCompiler<'_> {
    /// Compiles the `recipes` object, recording every problem.
    pub(super) fn recipes(&mut self, groups: &[(String, Json)], at: &Pointer) -> Recipes {
        let mut result = Recipes {
            recipes: Vec::new(),
            routing: Routing::default(),
            features: FeatureSet::default(),
        };
        if let Err(problem) = keys(groups, &GROUPS, &["fallbacks", "classifications"], at) {
            self.shared.problems.push(problem);
        }
        let mut ids: Vec<String> = Vec::new();
        let mut fallbacks: HashMap<Box<str>, usize> = HashMap::new();
        let mut broken_fallback = false;
        for group in GROUPS {
            let group_at = at.key(group);
            let Some((_, raw)) = groups.iter().find(|(key, _)| key == group) else {
                continue;
            };
            let raw = match list(raw, &group_at) {
                Ok(raw) => raw,
                Err(problem) => {
                    self.shared.problems.push(problem);
                    continue;
                }
            };
            for (index, raw) in raw.iter().enumerate() {
                let recipe_at = group_at.index(index);
                match self.recipe(group, raw, &recipe_at, &mut ids) {
                    Ok((recipe, selectors, features)) => {
                        result.features = result.features.union(features);
                        let position = result.recipes.len();
                        let destination = match group {
                            "fallbacks" => &mut fallbacks,
                            "classifications" => &mut result.routing.classifications,
                            _ => &mut result.routing.relations,
                        };
                        let selectors_at = recipe_at.key("selector").key(if group == "fallbacks" {
                            "kind"
                        } else {
                            "types"
                        });
                        for selector in selectors {
                            if destination.contains_key(selector.as_str()) {
                                let message = format!("Duplicate {group} selector: {selector}.");
                                self.shared
                                    .problems
                                    .push(Problem::value(message).at(&selectors_at));
                            } else {
                                destination.insert(selector.into(), position);
                            }
                        }
                        result.recipes.push(recipe);
                    }
                    Err(fault) => {
                        fault.record(&mut self.shared.problems);
                        broken_fallback |= group == "fallbacks";
                    }
                }
            }
        }
        match (fallbacks.get("unclassified"), fallbacks.get("classified")) {
            _ if broken_fallback => {}
            (Some(unclassified), Some(classified)) => {
                result.routing.unclassified = *unclassified;
                result.routing.classified = *classified;
            }
            _ => {
                let message = "Missing required classified/unclassified fallback recipes.";
                self.shared
                    .problems
                    .push(Problem::value(message).at(&at.key("fallbacks")));
            }
        }
        result
    }

    /// One recipe and the types it selects; problems inside its program are recorded, not returned.
    fn recipe(
        &mut self,
        group: &str,
        raw: &Json,
        at: &Pointer,
        ids: &mut Vec<String>,
    ) -> Result<(Recipe, Vec<String>, FeatureSet), Fault> {
        let recipe = object(raw, at)?;
        let allowed = [
            "id",
            "selector",
            "enabled",
            "bindings",
            "values",
            "steps",
            "description",
            "label",
        ];
        keys(recipe, &allowed, &["id", "selector", "steps"], at)?;
        descriptive(recipe, at)?;
        let get = |key: &str| {
            recipe
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        };
        let id = get("id")
            .and_then(Json::as_str)
            .filter(|id| !id.is_empty() && !ids.iter().any(|known| known == id));
        let Some(id) = id else {
            let shown = get("id").map_or_else(String::new, |id| {
                id.as_str()
                    .map_or_else(|| "that is not text".to_string(), str::to_string)
            });
            return Err(
                Problem::value(format!("Invalid or duplicate recipe id: {shown}."))
                    .at(&at.key("id"))
                    .into(),
            );
        };
        ids.push(id.to_string());
        let selector_at = at.key("selector");
        let selector = object(get("selector").unwrap_or(&Json::Null), &selector_at)?;
        let fallback = group == "fallbacks";
        let selector_keys: &[&str] = if fallback {
            &["kind"]
        } else {
            &["kind", "types"]
        };
        keys(selector, selector_keys, selector_keys, &selector_at)?;
        let kind = selector
            .iter()
            .find(|(key, _)| key == "kind")
            .and_then(|(_, kind)| kind.as_str())
            .unwrap_or_default();
        let allowed_kinds: &[&str] = match group {
            "fallbacks" => &["classified", "unclassified"],
            "classifications" => &["classification"],
            _ => &["relation"],
        };
        if !allowed_kinds.contains(&kind) {
            return Err(
                Problem::value(format!("Invalid selector kind for {group}."))
                    .at(&selector_at.key("kind"))
                    .into(),
            );
        }
        let bindings_at = at.key("bindings");
        let bindings = match get("bindings") {
            Some(bindings) => compile_bindings(object(bindings, &bindings_at)?, &bindings_at)?,
            None => Vec::new(),
        };
        let relation = group == "relations";
        if relation && !bindings.is_empty() {
            return Err(
                Problem::value("Relation recipes cannot declare classification bindings.")
                    .at(&bindings_at)
                    .into(),
            );
        }
        let selectors = if fallback {
            vec![kind.to_string()]
        } else {
            let types_at = selector_at.key("types");
            let raw = list(
                selector
                    .iter()
                    .find(|(key, _)| key == "types")
                    .map_or(&Json::Null, |(_, types)| types),
                &types_at,
            )?;
            let selectors: Option<Vec<String>> = raw
                .iter()
                .map(|item| {
                    item.as_str()
                        .filter(|text| !text.is_empty())
                        .map(str::to_string)
                })
                .collect();
            match selectors {
                Some(selectors) if !selectors.is_empty() => selectors,
                _ => {
                    return Err(Problem::value("Selectors require nonempty type strings.")
                        .at(&types_at)
                        .into());
                }
            }
        };
        let roots = roots(relation, kind == "unclassified", &bindings);
        let program_keys = ["enabled", "values", "steps"];
        let definition: Vec<(String, Json)> = recipe
            .iter()
            .filter(|(key, _)| program_keys.contains(&key.as_str()))
            .cloned()
            .collect();
        let built = self.program(&definition, roots, at, false, HashMap::new())?;
        let scene = if relation {
            SceneKind::Relation
        } else {
            SceneKind::Entity
        };
        let bindings = bindings.into_iter().map(|(_, binding)| binding).collect();
        Ok((
            Recipe {
                id: id.into(),
                scene,
                bindings,
                program: built.program,
            },
            selectors,
            built.features,
        ))
    }
}

/// The scopes a recipe reads: relations read the relation and its endpoints; entity recipes read the row, whose
/// classification routing proves present outside `unclassified`, and their bindings.
fn roots(relation: bool, unclassified: bool, bindings: &[(String, Binding)]) -> Roots {
    let t = types();
    if relation {
        return Roots {
            parameters: None,
            scene: Some((SceneKind::Relation, Arc::clone(&t.relation_scene))),
            bindings: None,
        };
    }
    let scene = if unclassified {
        Arc::clone(&t.scene)
    } else {
        let fields = t
            .scene
            .fields
            .iter()
            .map(|(key, kind)| {
                let kind = if &**key == "classification" {
                    Arc::new(kind.present())
                } else {
                    Arc::clone(kind)
                };
                (Arc::clone(key), kind)
            })
            .collect();
        Arc::new(ValueType {
            fields,
            ..(*t.scene).clone()
        })
    };
    let fields = bindings
        .iter()
        .map(|(name, binding)| (Arc::from(name.as_str()), binding_type(binding.shape)))
        .collect();
    let bindings = Arc::new(ValueType {
        fields,
        ..ValueType::scalar(TypeName::Bindings)
    });
    Roots {
        parameters: None,
        scene: Some((SceneKind::Entity, scene)),
        bindings: Some(bindings),
    }
}

/// The type recipes read a binding as; null without the attribute.
fn binding_type(shape: BindingShape) -> Type {
    let t = types();
    let kind = match shape {
        BindingShape::Bool => &t.boolean,
        BindingShape::Number => &t.number,
        BindingShape::HighestScoreColor => &t.color_binding,
    };
    Arc::new(kind.optional())
}

fn compile_bindings(
    raw: &[(String, Json)],
    at: &Pointer,
) -> Result<Vec<(String, Binding)>, Problem> {
    let mut bindings = Vec::with_capacity(raw.len());
    for (name, value) in raw {
        let at = at.key(name);
        let binding = object(value, &at)?;
        let fields = ["source", "attribute", "value_shape", "picker"];
        keys(binding, &fields, &fields, &at)?;
        let text = |key: &str| {
            binding
                .iter()
                .find(|(name, _)| name == key)
                .and_then(|(_, value)| value.as_str())
        };
        let shape = match (text("value_shape"), text("picker")) {
            (Some("bool"), Some("value")) => Some(BindingShape::Bool),
            (Some("number"), Some("value")) => Some(BindingShape::Number),
            (Some("color_classification_list"), Some("highest_score")) => {
                Some(BindingShape::HighestScoreColor)
            }
            _ => None,
        };
        let attribute = text("attribute").filter(|attribute| !attribute.is_empty());
        match (text("source"), shape, attribute) {
            (Some("primary_classification_attribute"), Some(shape), Some(attribute)) => {
                bindings.push((
                    name.clone(),
                    Binding {
                        attribute: attribute.into(),
                        shape,
                    },
                ));
            }
            _ => return Err(Problem::value("Unsupported typed attribute binding.").at(&at)),
        }
    }
    Ok(bindings)
}
