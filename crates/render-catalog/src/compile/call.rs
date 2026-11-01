//! Compiling a `call`: arguments, result types, arithmetic dimensions and folding of constant operations.

use std::sync::Arc;

use super::coerce::{fit, union};
use super::expr::{CExpr, Fault, Shared};
use super::fold;
use super::program::{Guards, ProgramCompiler};
use crate::definitions::{Operation, OperationKind, Type, types};
use crate::diagnostics::{Pointer, Problem, codes};
use crate::document::Json;
use crate::ir::{Arithmetic, Node, Op, Operand, TypeName};
use crate::validate::structure::{guard, keys, list};

impl ProgramCompiler<'_> {
    /// Compiles a call of the operation `name` with the raw `args` at `at`.
    pub(super) fn call(
        &mut self,
        shared: &mut Shared,
        name: &str,
        args: &[(String, Json)],
        at: &Pointer,
        guards: &Guards,
        depth: usize,
    ) -> Result<CExpr, Fault> {
        let Some(operation) = Operation::named(name) else {
            return Err(Problem::new(
                codes::UNKNOWN_OPERATION,
                format!("Unknown operation '{name}'."),
            )
            .at(at)
            .into());
        };
        let allowed: Vec<&str> = operation.arguments.iter().map(|(key, _)| *key).collect();
        let required: Vec<&str> = allowed
            .iter()
            .copied()
            .filter(|key| !operation.optional.contains(key))
            .collect();
        keys(args, &allowed, &required, at)?;
        let get = |key: &str| {
            args.iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        };
        if operation.kind == OperationKind::Op(Op::InsetBox)
            && get("margin").is_some() == get("fraction").is_some()
        {
            return Err(
                Problem::value("inset_box requires exactly one of margin or fraction.")
                    .at(at)
                    .into(),
            );
        }
        let args_at = at.key("args");
        let loc = shared.locations.loc(at);
        let foldable = self.guard_depth == 0;
        match operation.kind {
            OperationKind::If => {
                let raw = |key| get(key).unwrap_or(&Json::Null);
                let mut then_guards = guards.clone();
                then_guards.extend(guard(get("condition")));
                let t = types();
                let condition = self.expression(
                    shared,
                    raw("condition"),
                    &args_at.key("condition"),
                    guards,
                    Some(&t.boolean),
                    depth + 1,
                    true,
                )?;
                let then = self.expression(
                    shared,
                    raw("then"),
                    &args_at.key("then"),
                    &then_guards,
                    Some(&t.any),
                    depth + 1,
                    true,
                )?;
                let other = self.expression(
                    shared,
                    raw("else"),
                    &args_at.key("else"),
                    guards,
                    Some(&t.any),
                    depth + 1,
                    true,
                )?;
                let kind = union(&then.kind, &other.kind).map_err(|problem| problem.at(at))?;
                if foldable
                    && let Some(Json::Bool(chosen)) = condition.constant_value()
                    && then.constant_value().is_some()
                    && other.constant_value().is_some()
                {
                    return Ok(fit(if *chosen { then } else { other }, &kind)?);
                }
                let branches = [
                    condition.lower(),
                    fit(then, &kind)?.lower(),
                    fit(other, &kind)?.lower(),
                ];
                Ok(CExpr::node(Node::If(Box::new(branches)), kind, loc, at))
            }
            OperationKind::Coalesce => {
                let items = self.items(shared, get("values"), at, guards, depth, true)?;
                if items.is_empty() {
                    return Err(Problem::value("coalesce requires at least one value.")
                        .at(at)
                        .into());
                }
                let mut kind = Arc::clone(&items[0].kind);
                for item in &items[1..] {
                    kind = union(&kind, &item.kind).map_err(|problem| problem.at(at))?;
                }
                if items.iter().any(|item| !item.kind.nullable) {
                    kind = Arc::new(kind.present());
                }
                if foldable && items.iter().all(|item| item.constant_value().is_some()) {
                    let first = items
                        .iter()
                        .find(|item| item.constant_value() != Some(&Json::Null));
                    let value = first
                        .and_then(CExpr::constant_value)
                        .cloned()
                        .unwrap_or(Json::Null);
                    return Ok(CExpr::constant(value, kind, loc, at)?);
                }
                let optional = Arc::new(kind.optional());
                let items = items
                    .into_iter()
                    .map(|item| fit(item, &optional).map(CExpr::lower))
                    .collect::<Result<_, _>>()?;
                Ok(CExpr::node(Node::Coalesce(items), kind, loc, at))
            }
            OperationKind::Arithmetic(op) => {
                self.arithmetic(shared, op, operation, args, at, guards, depth)
            }
            OperationKind::Op(op) => {
                let mut children = Vec::with_capacity(operation.arguments.len());
                for (key, kind) in &operation.arguments {
                    let raw = get(key).unwrap_or(&Json::Null);
                    children.push(self.expression(
                        shared,
                        raw,
                        &args_at.key(key),
                        guards,
                        Some(kind),
                        depth + 1,
                        false,
                    )?);
                }
                let mut result = Arc::clone(&operation.result);
                match op {
                    Op::IsNotEmpty => {
                        let kind = &children[0].kind;
                        if !matches!(kind.name, TypeName::Null | TypeName::String)
                            && kind.item.is_none()
                        {
                            return Err(Problem::value(
                                "is_not_empty requires string, list or null.",
                            )
                            .at(at)
                            .into());
                        }
                    }
                    Op::TrimText | Op::LookupText if !children[0].kind.nullable => {
                        result = Arc::clone(&types().string)
                    }
                    _ => {}
                }
                if foldable && let Some(value) = fold::operation(op, &children) {
                    return Ok(CExpr::constant(value, result, loc, at)?);
                }
                let args = children.into_iter().map(CExpr::lower).collect();
                Ok(CExpr::node(Node::Call { op, args }, result, loc, at))
            }
        }
    }

    /// Compiles the `values` list of a call, each item as a branch when `guarded`.
    fn items(
        &mut self,
        shared: &mut Shared,
        raw: Option<&Json>,
        at: &Pointer,
        guards: &Guards,
        depth: usize,
        guarded: bool,
    ) -> Result<Vec<CExpr>, Fault> {
        let raw = list(raw.unwrap_or(&Json::Null), at)?;
        let values_at = at.key("args").key("values");
        let mut items = Vec::with_capacity(raw.len());
        for (index, value) in raw.iter().enumerate() {
            items.push(self.expression(
                shared,
                value,
                &values_at.index(index),
                guards,
                None,
                depth + 1,
                guarded,
            )?);
        }
        Ok(items)
    }

    #[allow(clippy::too_many_arguments)] // The arguments are the compile position, kept apart for clarity.
    fn arithmetic(
        &mut self,
        shared: &mut Shared,
        op: Arithmetic,
        operation: &Operation,
        args: &[(String, Json)],
        at: &Pointer,
        guards: &Guards,
        depth: usize,
    ) -> Result<CExpr, Fault> {
        let operands = if matches!(
            op,
            Arithmetic::Add | Arithmetic::Mul | Arithmetic::Min | Arithmetic::Max
        ) {
            let values = args
                .iter()
                .find(|(key, _)| key == "values")
                .map(|(_, value)| value);
            self.items(shared, values, at, guards, depth, false)?
        } else {
            let mut operands = Vec::with_capacity(operation.arguments.len());
            for (key, kind) in &operation.arguments {
                let raw = args
                    .iter()
                    .find(|(name, _)| name == key)
                    .map_or(&Json::Null, |(_, value)| value);
                operands.push(self.expression(
                    shared,
                    raw,
                    &at.key("args").key(key),
                    guards,
                    Some(kind),
                    depth + 1,
                    false,
                )?);
            }
            operands
        };
        let numeric = |kind: &Type| {
            !kind.nullable
                && matches!(
                    kind.name,
                    TypeName::Number | TypeName::Integer | TypeName::Length
                )
        };
        if operands.is_empty() || !operands.iter().all(|operand| numeric(&operand.kind)) {
            let message = "Arithmetic requires non-null numbers or lengths.";
            return Err(Problem::new(codes::TYPE_MISMATCH, message).at(at).into());
        }
        let lengths: Vec<bool> = operands
            .iter()
            .map(|operand| operand.kind.name == TypeName::Length)
            .collect();
        let count = lengths.iter().filter(|length| **length).count();
        let (valid, length) = match op {
            Arithmetic::Mul => (count <= 1, count == 1),
            Arithmetic::Div => (lengths != [false, true], lengths == [true, false]),
            _ => (count == 0 || count == lengths.len(), count > 0),
        };
        if !valid {
            return Err(
                Problem::new(codes::TYPE_MISMATCH, "Incompatible arithmetic dimensions.")
                    .at(at)
                    .into(),
            );
        }
        let t = types();
        let kind = Arc::clone(if length { &t.length } else { &t.number });
        let loc = shared.locations.loc(at);
        if self.guard_depth == 0
            && count == 0
            && let Some(value) = fold::arithmetic(op, &operands)
        {
            return Ok(CExpr::constant(value, kind, loc, at)?);
        }
        let operands = operands
            .into_iter()
            .zip(lengths)
            .map(|(operand, length)| Operand {
                value: operand.lower(),
                length,
            })
            .collect();
        Ok(CExpr::node(
            Node::Arithmetic {
                op,
                operands,
                length,
            },
            kind,
            loc,
            at,
        ))
    }
}
