//! Compiling the expressions of one program: literals, records, lists, references and calculated values.

use std::collections::HashMap;
use std::sync::Arc;

use super::coerce::{coerce, fit, literal_type, union};
use super::expr::{CExpr, Fault, Shared};
use super::scene::{context_field, scene_field};
use crate::definitions::{MAX_DEPTH, Type, types};
use crate::diagnostics::{Pointer, Problem, codes};
use crate::document::Json;
use crate::ir::{Expr, Node, SceneKind, TypeName, ValueType};
use crate::validate::structure::{keys, list, object};

/// Reference paths known to be present: each proven by an `is_present` or `is_not_empty` condition.
pub(super) type Guards = Vec<Vec<String>>;

/// The scopes a program reads besides its own calculated values.
pub(super) struct Roots {
    /// A template's parameters record.
    pub(super) parameters: Option<Type>,
    /// A recipe's row.
    pub(super) scene: Option<(SceneKind, Type)>,
    /// An entity recipe's bindings record.
    pub(super) bindings: Option<Type>,
}

/// Compiles the expressions of one program and collects its slots.
pub(super) struct ProgramCompiler<'d> {
    roots: Roots,
    at: Pointer,
    names: Vec<&'d str>,
    raw: Vec<&'d Json>,
    compiled: Vec<Option<Result<CExpr, ()>>>,
    active: Vec<bool>,
    /// References the program's own condition proves present; set before values are compiled.
    pub(super) base_guards: Guards,
    /// How many conditional branches enclose the expression being compiled; operations inside are not folded.
    pub(super) guard_depth: usize,
    /// Call-site constants a specialized template's parameter references fold to.
    pub(super) statics: HashMap<String, Json>,
    reads: Vec<(Vec<String>, Type, Expr)>,
}

impl<'d> ProgramCompiler<'d> {
    /// A compiler for the program at `at` reading `roots`, whose calculated values are `values`.
    pub(super) fn new(roots: Roots, at: &Pointer, values: &'d [(String, Json)]) -> Self {
        let mut sorted: Vec<&'d (String, Json)> = values.iter().collect();
        sorted.sort_by(|left, right| left.0.cmp(&right.0));
        Self {
            roots,
            at: at.clone(),
            names: sorted.iter().map(|(name, _)| name.as_str()).collect(),
            raw: sorted.iter().map(|(_, value)| value).collect(),
            compiled: vec![None; sorted.len()],
            active: vec![false; sorted.len()],
            base_guards: Guards::new(),
            guard_depth: 0,
            statics: HashMap::new(),
            reads: Vec::new(),
        }
    }

    /// How many calculated values the program has.
    pub(super) fn value_count(&self) -> usize {
        self.names.len()
    }

    /// The calculated value `index` compiled, compiling it on first use; a failure is recorded once.
    pub(super) fn value(
        &mut self,
        shared: &mut Shared,
        index: usize,
    ) -> Result<(Type, Option<Json>), Fault> {
        let name = self.names[index];
        if let Some(done) = &self.compiled[index] {
            return done
                .as_ref()
                .map(|value| (Arc::clone(&value.kind), value.constant_value().cloned()))
                .map_err(|()| Fault::Reported);
        }
        if self.active[index] {
            let message = format!("Calculated-value cycle at {name}.");
            return Err(Problem::new(codes::VALUE_CYCLE, message)
                .at(&self.at.key("values").key(name))
                .into());
        }
        if self.active.iter().filter(|active| **active).count() >= MAX_DEPTH {
            return Err(Problem::new(
                codes::EXECUTION_LIMIT,
                "Calculated-value depth limit exceeded.",
            )
            .at(&self.at)
            .into());
        }
        self.active[index] = true;
        let at = self.at.key("values").key(name);
        let guards = self.base_guards.clone();
        let result = self.expression(shared, self.raw[index], &at, &guards, None, 0, true);
        self.active[index] = false;
        let stored = match result {
            Ok(value) => Ok(value),
            Err(fault) => {
                fault.record(&mut shared.problems);
                Err(())
            }
        };
        self.compiled[index] = Some(stored);
        self.value(shared, index)
    }

    /// Compiles the raw expression `raw` at `at`, coerced to `expected` when given. `guards` lists the references
    /// known present there, `depth` counts nesting and `guarded` marks a conditional branch.
    #[allow(clippy::too_many_arguments)] // The arguments are the compile position, kept apart for clarity.
    pub(super) fn expression(
        &mut self,
        shared: &mut Shared,
        raw: &Json,
        at: &Pointer,
        guards: &Guards,
        expected: Option<&Type>,
        depth: usize,
        guarded: bool,
    ) -> Result<CExpr, Fault> {
        if depth > MAX_DEPTH {
            return Err(
                Problem::new(codes::EXECUTION_LIMIT, "Expression depth limit exceeded.")
                    .at(at)
                    .into(),
            );
        }
        self.guard_depth += usize::from(guarded);
        let result = self
            .unchecked(shared, raw, at, guards, depth)
            .and_then(|expression| match expected {
                Some(expected) => Ok(coerce(expression, expected, false)?),
                None => Ok(expression),
            });
        self.guard_depth -= usize::from(guarded);
        result.map_err(|fault| fault.or_at(at))
    }

    fn unchecked(
        &mut self,
        shared: &mut Shared,
        raw: &Json,
        at: &Pointer,
        guards: &Guards,
        depth: usize,
    ) -> Result<CExpr, Fault> {
        let loc = shared.locations.loc(at);
        let Json::Object(members) = raw else {
            if let Json::Array(items) = raw {
                return self.list(shared, items, at, guards, depth);
            }
            return Ok(CExpr::constant(raw.clone(), literal_type(raw), loc, at)?);
        };
        if let Some(value) = raw.get("literal") {
            keys(members, &["literal"], &["literal"], at)?;
            return Ok(CExpr::constant(
                value.clone(),
                literal_type(value),
                loc,
                at,
            )?);
        }
        if let Some(path) = raw.get("ref") {
            keys(members, &["ref"], &["ref"], at)?;
            let parts: Option<Vec<&str>> = list(path, at)?.iter().map(Json::as_str).collect();
            let parts =
                parts.filter(|parts| parts.len() >= 2 && parts.iter().all(|part| !part.is_empty()));
            let Some(parts) = parts else {
                return Err(Problem::value(
                    "Reference must contain a scope and exact field names.",
                )
                .at(at)
                .into());
            };
            return self.reference(shared, &parts, at, guards);
        }
        if let Some(name) = raw.get("call") {
            keys(members, &["call", "args"], &["call", "args"], at)?;
            let args = object(raw.get("args").unwrap_or(&Json::Null), &at.key("args"))?;
            let Some(name) = name.as_str() else {
                return Err(
                    Problem::new(codes::UNKNOWN_OPERATION, "Operation names are text.")
                        .at(&at.key("call"))
                        .into(),
                );
            };
            return self.call(shared, name, args, at, guards, depth);
        }
        let mut fields = Vec::with_capacity(members.len());
        for (key, value) in members {
            let member =
                self.expression(shared, value, &at.key(key), guards, None, depth + 1, false)?;
            fields.push((Arc::<str>::from(key.as_str()), member));
        }
        let record = CExpr::record_literal(fields, loc, at)?;
        let is_length = record.kind.fields.len() == 2
            && record.kind.field("value").is_some()
            && record.kind.field("unit").is_some();
        Ok(if is_length {
            coerce(record, &types().length, false)?
        } else {
            record
        })
    }

    fn list(
        &mut self,
        shared: &mut Shared,
        raw: &[Json],
        at: &Pointer,
        guards: &Guards,
        depth: usize,
    ) -> Result<CExpr, Fault> {
        let mut items = Vec::with_capacity(raw.len());
        for (index, value) in raw.iter().enumerate() {
            items.push(self.expression(
                shared,
                value,
                &at.index(index),
                guards,
                None,
                depth + 1,
                false,
            )?);
        }
        let t = types();
        let mut item = items
            .first()
            .map_or_else(|| Arc::clone(&t.empty), |first| Arc::clone(&first.kind));
        for other in items.iter().skip(1) {
            match union(&item, &other.kind) {
                Ok(joined) => item = joined,
                Err(_) => {
                    item = Arc::clone(&t.any);
                    break;
                }
            }
        }
        let kind = Arc::new(ValueType {
            item: Some(Arc::clone(&item)),
            ..ValueType::scalar(TypeName::List)
        });
        let loc = shared.locations.loc(at);
        if items.iter().all(|item| item.constant_value().is_some()) {
            let value = items
                .iter()
                .map(|item| item.constant_value().cloned().unwrap_or(Json::Null))
                .collect();
            return Ok(CExpr::constant(Json::Array(value), kind, loc, at)?);
        }
        let items = items
            .into_iter()
            .map(|value| fit(value, &item).map(CExpr::lower))
            .collect::<Result<_, _>>()?;
        Ok(CExpr::node(Node::List(items), kind, loc, at))
    }

    /// Compiles the reference `scope, name, fields…` to a calculated value, a parameter, the render context, the row or
    /// a binding; a prefix proven present by `guards` reads as present.
    fn reference(
        &mut self,
        shared: &mut Shared,
        path: &[&str],
        at: &Pointer,
        guards: &Guards,
    ) -> Result<CExpr, Fault> {
        let loc = shared.locations.loc(at);
        let (scope, name, tail) = (path[0], path[1], &path[2..]);
        let unknown_field = |name: &str, kind: &ValueType| {
            Problem::new(
                codes::UNKNOWN_REFERENCE,
                format!("Unknown field '{name}' on {}.", kind.name.as_str()),
            )
            .at(at)
        };
        let root = |kind: &Option<Type>| {
            kind.clone().ok_or_else(|| {
                Problem::new(
                    codes::UNKNOWN_REFERENCE,
                    format!("Unknown scope '{scope}'."),
                )
                .at(at)
            })
        };
        let (mut kind, source, constant) = match scope {
            "values" => {
                let Some(index) = self.names.iter().position(|candidate| *candidate == name) else {
                    let message = format!("Unknown calculated value: {name}.");
                    return Err(Problem::new(codes::UNKNOWN_REFERENCE, message)
                        .at(at)
                        .into());
                };
                let (kind, constant) = self.value(shared, index)?;
                (kind, Node::Slot(index as u32), constant)
            }
            "parameters" | "bindings" => {
                let record = root(if scope == "parameters" {
                    &self.roots.parameters
                } else {
                    &self.roots.bindings
                })?;
                let (index, kind) = record
                    .field(name)
                    .ok_or_else(|| unknown_field(name, &record))?;
                let node = if scope == "parameters" {
                    Node::Parameter(index as u32)
                } else {
                    Node::Binding(index as u32)
                };
                (Arc::clone(kind), node, None)
            }
            "context" => {
                let context = &types().context;
                let field = context_field(name).ok_or_else(|| unknown_field(name, context))?;
                (Arc::clone(&types().number), Node::Context(field), None)
            }
            "scene" => {
                let record = root(&self.roots.scene.as_ref().map(|(_, kind)| Arc::clone(kind)))?;
                let (_, kind) = record
                    .field(name)
                    .ok_or_else(|| unknown_field(name, &record))?;
                (Arc::clone(kind), Node::Const(crate::ir::Value::Null), None)
            }
            _ => {
                return Err(Problem::new(
                    codes::UNKNOWN_REFERENCE,
                    format!("Unknown scope '{scope}'."),
                )
                .at(at)
                .into());
            }
        };
        let proven = |length: usize| {
            guards
                .iter()
                .any(|guard| guard.len() == length && guard.iter().zip(path).all(|(a, b)| a == b))
        };
        if proven(2) {
            kind = Arc::new(kind.present());
        }
        let base = Arc::clone(&kind);
        let mut indices = Vec::with_capacity(tail.len());
        for (offset, part) in tail.iter().enumerate() {
            let nullable = kind.nullable;
            let (index, field) = kind.field(part).ok_or_else(|| unknown_field(part, &kind))?;
            indices.push(index as u32);
            kind = Arc::clone(field);
            if nullable {
                kind = Arc::new(kind.optional());
            }
            if proven(offset + 3) {
                kind = Arc::new(kind.present());
            }
        }
        let folded = match (scope, constant) {
            ("parameters", _) => self
                .statics
                .get(name)
                .cloned()
                .map(|value| descend(value, tail)),
            ("values", Some(value)) => Some(descend(value, tail)),
            _ => None,
        };
        if let Some(value) = folded
            && (value != Json::Null || kind.nullable)
        {
            return Ok(CExpr::constant(value, kind, loc, at)?);
        }
        if scope == "scene" {
            return Ok(self.read(shared, path, kind, at));
        }
        let node = if indices.is_empty() {
            source
        } else {
            Node::Field {
                value: Box::new(Expr {
                    node: source,
                    kind: base,
                    loc,
                }),
                path: indices.into(),
            }
        };
        Ok(CExpr::node(node, kind, loc, at))
    }

    /// The slot holding the validated read of the row at `path` as `kind`, shared by every read of that path and type.
    fn read(&mut self, shared: &mut Shared, path: &[&str], kind: Type, at: &Pointer) -> CExpr {
        let loc = shared.locations.loc(at);
        let key: Vec<String> = path.iter().map(|part| part.to_string()).collect();
        let existing = self
            .reads
            .iter()
            .position(|(path, read_kind, _)| *path == key && *read_kind == kind);
        let index = existing.unwrap_or_else(|| {
            let scene = self
                .roots
                .scene
                .as_ref()
                .map_or(SceneKind::Entity, |(scene, _)| *scene);
            let (field, field_kind, rest) = scene_field(scene, &path[1..]);
            let unlocated = shared.locations.loc(&Pointer::root());
            let mut value = Expr {
                node: Node::Scene(field),
                kind: field_kind,
                loc: unlocated,
            };
            if !rest.is_empty() {
                value = Expr {
                    node: Node::Field {
                        value: Box::new(value),
                        path: rest.into(),
                    },
                    kind: Arc::clone(&kind),
                    loc: unlocated,
                };
            }
            let validated = Expr {
                node: Node::Validate(Box::new(value)),
                kind: Arc::clone(&kind),
                loc,
            };
            self.reads.push((key, Arc::clone(&kind), validated));
            self.reads.len() - 1
        });
        CExpr::node(Node::Slot((self.names.len() + index) as u32), kind, loc, at)
    }

    /// The program's slots: calculated values in name order, then the validated reads of the row.
    pub(super) fn finish(self) -> Box<[Expr]> {
        let any = &types().any;
        let values = self.compiled.into_iter().map(|value| match value {
            Some(Ok(value)) => value.lower(),
            _ => Expr {
                node: Node::Const(crate::ir::Value::Null),
                kind: Arc::clone(any),
                loc: crate::ir::Loc(0),
            },
        });
        values
            .chain(self.reads.into_iter().map(|(_, _, read)| read))
            .collect()
    }
}

/// The part of the constant `value` at the record `path`; null once a record on the way is null or lacks the field.
fn descend(value: Json, path: &[&str]) -> Json {
    path.iter().fold(value, |value, key| {
        value.get(key).cloned().unwrap_or(Json::Null)
    })
}
