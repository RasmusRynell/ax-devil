//! Compiling one program: its condition, calculated values and ordered steps.

use std::collections::HashMap;
use std::sync::Arc;

use super::coerce::{coerce, literal_type};
use super::expr::{CExpr, Fault, Form};
use super::program::{Guards, ProgramCompiler, Roots};
use super::templates::CatalogCompiler;
use crate::definitions::{MAX_EXPANDED_STEPS, MAX_TEMPLATE_DEPTH, types};
use crate::diagnostics::{Pointer, Problem, codes};
use crate::document::Json;
use crate::ir::{Action, Call, Draw, Node, Program, Step, Value};
use crate::validate::structure::{descriptive, guard, keys, list, object};
use crate::{FeatureSet, OverlayFeature};

const TRUE: Json = Json::Bool(true);

/// One compiled program with what its callers need to know.
pub(super) struct Built {
    pub(super) program: Program,
    /// Steps it runs with every call expanded, capped just past the limit.
    pub(super) expanded: usize,
    /// Its template nesting, itself included.
    pub(super) depth: usize,
    /// Every feature tag on what it can draw.
    pub(super) features: FeatureSet,
}

/// What compiling one step adds to its program.
struct StepOutcome {
    /// The step, unless it never runs.
    step: Option<Step>,
    /// The feature tags along each way it draws.
    outputs: Vec<FeatureSet>,
    /// Every feature tag on what it draws.
    features: FeatureSet,
    expanded: usize,
    depth: usize,
}

impl CatalogCompiler<'_> {
    /// Compiles the program `definition` at `at`. A template (`template`) checks its own keys and may carry a
    /// feature; `statics` are constant inputs of a specialized template.
    pub(super) fn program(
        &mut self,
        definition: &[(String, Json)],
        roots: Roots,
        at: &Pointer,
        template: bool,
        statics: HashMap<String, Json>,
    ) -> Result<Built, Fault> {
        if template {
            let allowed = [
                "parameters",
                "values",
                "steps",
                "enabled",
                "description",
                "label",
                "feature",
            ];
            keys(definition, &allowed, &["steps"], at)?;
            descriptive(definition, at)?;
        }
        let get = |key: &str| {
            definition
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        };
        let feature = feature(get("feature"), at)?;
        let values = match get("values") {
            Some(values) => object(values, &at.key("values"))?,
            None => &[],
        };
        let raw_steps = list(get("steps").unwrap_or(&Json::Null), &at.key("steps"))?;
        let mut compiler = ProgramCompiler::new(roots, at, values);
        compiler.statics = statics;
        let shared = &mut self.shared;
        let enabled_at = at.key("enabled");
        let raw_enabled = get("enabled").unwrap_or(&TRUE);
        let enabled = compiler.expression(
            shared,
            raw_enabled,
            &enabled_at,
            &Guards::new(),
            Some(&types().boolean),
            0,
            false,
        );
        let enabled = enabled.unwrap_or_else(|fault| {
            fault.record(&mut shared.problems);
            let loc = shared.locations.loc(&enabled_at);
            let always = Form::Node(Node::Const(Value::Bool(true)));
            CExpr {
                kind: Arc::clone(&types().boolean),
                loc,
                at: enabled_at.clone(),
                form: always,
            }
        });
        let guards: Guards = guard(get("enabled")).into_iter().collect();
        compiler.base_guards = guards.clone();
        for index in 0..compiler.value_count() {
            if let Err(fault) = compiler.value(&mut self.shared, index) {
                fault.record(&mut self.shared.problems);
            }
        }
        let (mut steps, mut outputs, mut features, mut expanded, mut depth) =
            (Vec::new(), Vec::new(), FeatureSet::default(), 0, 1);
        for (index, raw) in raw_steps.iter().enumerate() {
            let step_at = at.key("steps").index(index);
            match self.step(&mut compiler, raw, &step_at, &guards, &enabled) {
                Ok(compiled) => {
                    steps.extend(compiled.step);
                    outputs.extend(compiled.outputs);
                    features = features.union(compiled.features);
                    expanded = (expanded + compiled.expanded).min(MAX_EXPANDED_STEPS + 1);
                    depth = depth.max(compiled.depth);
                }
                Err(fault) => fault.record(&mut self.shared.problems),
            }
            if expanded > MAX_EXPANDED_STEPS || depth > MAX_TEMPLATE_DEPTH {
                let message = "Worst-case expanded execution limit exceeded.";
                return Err(Problem::new(codes::EXECUTION_LIMIT, message)
                    .at(&step_at)
                    .into());
            }
        }
        if enabled.is_false() || outputs.is_empty() {
            outputs.clear();
            features = FeatureSet::default();
        }
        let own = tags(feature);
        let outputs = minimal(outputs.into_iter().map(|tags| tags.union(own)).collect());
        let features = if outputs.is_empty() {
            features
        } else {
            features.union(own)
        };
        let loc = self.shared.locations.loc(at);
        let program = Program {
            loc,
            feature,
            enabled: enabled.lower(),
            slots: compiler.finish(),
            steps: steps.into(),
            outputs,
        };
        Ok(Built {
            program,
            expanded,
            depth,
            features,
        })
    }

    fn step(
        &mut self,
        compiler: &mut ProgramCompiler<'_>,
        raw: &Json,
        at: &Pointer,
        guards: &Guards,
        enabled: &CExpr,
    ) -> Result<StepOutcome, Fault> {
        let step = object(raw, at)?;
        let get = |key: &str| {
            step.iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        };
        let template = get("template");
        if template.is_some() {
            let allowed = [
                "template", "inputs", "enabled", "visible", "label", "feature",
            ];
            keys(step, &allowed, &["template"], at)?;
        } else {
            let allowed = [
                "primitive",
                "fields",
                "style",
                "enabled",
                "visible",
                "label",
                "feature",
            ];
            keys(step, &allowed, &["primitive", "fields"], at)?;
        }
        descriptive(step, at)?;
        let feature = feature(get("feature"), at)?;
        let visible = match get("visible") {
            None => true,
            Some(Json::Bool(visible)) => *visible,
            Some(_) => {
                return Err(Problem::value("visible must be true or false.")
                    .at(&at.key("visible"))
                    .into());
            }
        };
        let shared = &mut self.shared;
        let boolean = &types().boolean;
        let condition = compiler.expression(
            shared,
            get("enabled").unwrap_or(&TRUE),
            &at.key("enabled"),
            guards,
            Some(boolean),
            0,
            false,
        )?;
        let mut step_guards = guards.clone();
        step_guards.extend(guard(get("enabled")));
        let guarded = !condition.is_true() || !enabled.is_true();
        let kept = visible && !condition.is_false();
        let tags = tags(feature);
        let loc = shared.locations.loc(at);
        let Some(name) = template else {
            let name = get("primitive").and_then(Json::as_str).unwrap_or_default();
            let Some((primitive, fields_type, style_type)) = types().primitive(name) else {
                return Err(Problem::value(format!("Unknown primitive: {name}."))
                    .at(at)
                    .into());
            };
            let raw_fields = get("fields").unwrap_or(&Json::Null);
            let fields = compiler.expression(
                shared,
                raw_fields,
                &at.key("fields"),
                &step_guards,
                Some(fields_type),
                0,
                guarded,
            )?;
            let raw_style = get("style").cloned().unwrap_or(Json::Object(Vec::new()));
            let style = compiler.expression(
                shared,
                &raw_style,
                &at.key("style"),
                &step_guards,
                Some(style_type),
                0,
                guarded,
            )?;
            let action = Action::Draw(Draw {
                primitive,
                fields: fields.lower(),
                style: style.lower(),
            });
            let step = kept.then(|| Step {
                loc,
                feature,
                enabled: condition.lower(),
                action,
            });
            let (outputs, features) = if kept {
                (vec![tags], tags)
            } else {
                (Vec::new(), FeatureSet::default())
            };
            return Ok(StepOutcome {
                step,
                outputs,
                features,
                expanded: 1,
                depth: 1,
            });
        };
        let template_at = at.key("template");
        let name = name.as_str().ok_or_else(|| {
            Problem::new(codes::INVALID_STRUCTURE, "Expected text.").at(&template_at)
        })?;
        let index = self.find(name).ok_or_else(|| {
            Problem::new(
                codes::UNKNOWN_TEMPLATE,
                format!("Unknown template: {name}."),
            )
            .at(&template_at)
        })?;
        self.template(index, at)?;
        let inputs_at = if get("inputs").is_some() {
            at.key("inputs")
        } else {
            at.clone()
        };
        let inputs = match get("inputs") {
            Some(inputs) => object(inputs, &inputs_at)?,
            None => &[],
        };
        let template = &self.templates[index];
        let parameters = Arc::clone(&template.parameters);
        let defaults = template.defaults.clone();
        let mut missing: Vec<&str> = parameters
            .fields
            .iter()
            .map(|(key, _)| &**key)
            .filter(|key| {
                !inputs.iter().any(|(name, _)| name == key)
                    && !defaults.iter().any(|(name, _)| name == key)
            })
            .collect();
        if !missing.is_empty() {
            missing.sort_unstable();
            let message = format!("Missing required template inputs: {}.", missing.join(", "));
            return Err(Problem::value(message).at(at).into());
        }
        let shared = &mut self.shared;
        let (mut members, mut constants) = (Vec::new(), Vec::new());
        for (key, raw) in inputs {
            let input_at = inputs_at.key(key);
            let Some((_, kind)) = parameters.field(key) else {
                let message = format!("Unknown field '{key}' on parameters.");
                return Err(Problem::new(codes::UNKNOWN_REFERENCE, message)
                    .at(&input_at)
                    .into());
            };
            let input =
                compiler.expression(shared, raw, &input_at, &step_guards, None, 0, guarded)?;
            if let Some(value) = coerce(input.clone(), kind, false)?.constant_value() {
                constants.push((key.clone(), value.clone()));
            }
            members.push((Arc::<str>::from(key.as_str()), input));
        }
        for (key, value) in defaults
            .into_iter()
            .filter(|(key, _)| !inputs.iter().any(|(name, _)| name == key))
        {
            let loc = shared.locations.loc(&inputs_at);
            members.push((
                Arc::from(key.as_str()),
                CExpr::constant(value.clone(), literal_type(&value), loc, &inputs_at)?,
            ));
            constants.push((key, value));
        }
        let loc = shared.locations.loc(&inputs_at);
        let record = CExpr::record_literal(members, loc, &inputs_at)?;
        let inputs = coerce(record, &parameters, false)?;
        let target = self.specialize(index, constants, at)?;
        let outputs: Vec<FeatureSet> = if kept {
            target
                .program
                .outputs
                .iter()
                .map(|output| output.union(tags))
                .collect()
        } else {
            Vec::new()
        };
        let features = if outputs.is_empty() {
            FeatureSet::default()
        } else {
            target.features.union(tags)
        };
        let step = kept.then(|| Step {
            loc,
            feature,
            enabled: condition.lower(),
            action: Action::Call(Call {
                program: Arc::clone(&target.program),
                inputs: inputs.lower(),
            }),
        });
        Ok(StepOutcome {
            step,
            outputs,
            features,
            expanded: target.expanded + 1,
            depth: 1 + target.depth,
        })
    }
}

/// The overlay feature a definition is tagged with.
fn feature(raw: Option<&Json>, at: &Pointer) -> Result<Option<OverlayFeature>, Problem> {
    let Some(raw) = raw else { return Ok(None) };
    let feature = raw.as_str().and_then(OverlayFeature::parse);
    feature.map(Some).ok_or_else(|| {
        let shown = raw.as_str().unwrap_or("that is not text");
        Problem::value(format!("Unknown overlay feature: {shown}.")).at(&at.key("feature"))
    })
}

/// The set holding `feature`, if any.
fn tags(feature: Option<OverlayFeature>) -> FeatureSet {
    feature.map_or(FeatureSet::default(), |feature| {
        FeatureSet::default().with(feature)
    })
}

/// The tag sets without repeats or supersets of others: a way of drawing is hidden whenever a smaller one is.
fn minimal(mut outputs: Vec<FeatureSet>) -> Box<[FeatureSet]> {
    outputs.sort_by_key(|tags| tags.iter().count());
    let mut kept: Vec<FeatureSet> = Vec::new();
    for tags in outputs {
        if !kept.iter().any(|smaller| smaller.union(tags) == tags) {
            kept.push(tags);
        }
    }
    kept.into()
}
