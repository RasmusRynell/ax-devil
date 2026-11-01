//! Compile-time expressions: typed, located, folded to constants where possible, lowered to [`Expr`] at the end.

use std::collections::HashMap;
use std::sync::Arc;

use crate::definitions::Type;
use crate::diagnostics::{Pointer, Problem, Problems};
use crate::document::Json;
use crate::ir::{Expr, Loc, Node, TypeName, Value, ValueType};
use crate::validate::literal;

/// What every part of one compilation shares: interned locations and the problems found.
#[derive(Default)]
pub(super) struct Shared {
    pub(super) locations: Locations,
    pub(super) problems: Problems,
}

/// JSON pointers interned as [`Loc`]s.
#[derive(Default)]
pub(super) struct Locations {
    list: Vec<Box<str>>,
    index: HashMap<Pointer, Loc>,
}

impl Locations {
    /// The [`Loc`] of `at`.
    pub(super) fn loc(&mut self, at: &Pointer) -> Loc {
        if let Some(loc) = self.index.get(at) {
            return *loc;
        }
        let loc = Loc(u32::try_from(self.list.len()).unwrap_or(u32::MAX));
        self.list.push(at.as_str().into());
        self.index.insert(at.clone(), loc);
        loc
    }

    /// Every interned pointer, indexed by [`Loc`].
    pub(super) fn into_list(self) -> Box<[Box<str>]> {
        self.list.into()
    }
}

/// Why compiling a part stopped.
#[derive(Debug)]
pub(super) enum Fault {
    /// A problem not recorded yet.
    Problem(Problem),
    /// A problem already recorded, such as a failed calculated value another part refers to.
    Reported,
}

impl From<Problem> for Fault {
    fn from(problem: Problem) -> Self {
        Self::Problem(problem)
    }
}

impl Fault {
    /// The fault with an unlocated problem placed at `at`.
    pub(super) fn or_at(self, at: &Pointer) -> Self {
        match self {
            Self::Problem(problem) => Self::Problem(problem.or_at(at)),
            Self::Reported => Self::Reported,
        }
    }

    /// Records the fault's problem, if it has one not recorded yet.
    pub(super) fn record(self, problems: &mut Problems) {
        if let Self::Problem(problem) = self {
            problems.push(problem);
        }
    }
}

/// A compiled expression before lowering.
#[derive(Clone, Debug)]
pub(super) struct CExpr {
    pub(super) kind: Type,
    pub(super) loc: Loc,
    pub(super) at: Pointer,
    pub(super) form: Form,
}

/// What a [`CExpr`] is.
#[derive(Clone, Debug)]
pub(super) enum Form {
    /// A constant, as written and laid out for the expression's type.
    Const(Json, Value),
    /// A record built from named members, kept apart so a later coercion can rebuild it field by field. `check`
    /// marks a record whose type's constraints are checked once it is built.
    Members {
        members: Vec<(Arc<str>, CExpr)>,
        check: bool,
    },
    /// Anything else.
    Node(Node),
}

impl CExpr {
    /// The constant `value` as type `kind`, checked against that type; a failure is located at `at`.
    pub(super) fn constant(
        value: Json,
        kind: Type,
        loc: Loc,
        at: &Pointer,
    ) -> Result<Self, Problem> {
        let laid_out = literal(&value, &kind, kind.name.as_str())
            .map_err(|message| Problem::value(message).at(at))?;
        Ok(Self {
            kind,
            loc,
            at: at.clone(),
            form: Form::Const(value, laid_out),
        })
    }

    /// A record of `members` (in `kind`'s field order), folded to a constant when every member is one. `check`
    /// marks a record whose type's constraints are checked once it is built.
    pub(super) fn record(
        members: Vec<(Arc<str>, CExpr)>,
        kind: Type,
        check: bool,
        loc: Loc,
        at: &Pointer,
    ) -> Result<Self, Problem> {
        let constants: Option<Vec<(String, Json)>> = members
            .iter()
            .map(|(key, member)| Some((key.to_string(), member.constant_value()?.clone())))
            .collect();
        match constants {
            Some(constants) => Self::constant(Json::Object(constants), kind, loc, at),
            None => Ok(Self {
                kind,
                loc,
                at: at.clone(),
                form: Form::Members { members, check },
            }),
        }
    }

    /// A record literal of `members` in key order, typed by them.
    pub(super) fn record_literal(
        mut members: Vec<(Arc<str>, CExpr)>,
        loc: Loc,
        at: &Pointer,
    ) -> Result<Self, Problem> {
        members.sort_by(|left, right| left.0.cmp(&right.0));
        let fields = members
            .iter()
            .map(|(key, member)| (Arc::clone(key), Arc::clone(&member.kind)));
        let kind = Arc::new(ValueType {
            fields: fields.collect(),
            ..ValueType::scalar(TypeName::Record)
        });
        Self::record(members, kind, false, loc, at)
    }

    /// An expression computing `node`.
    pub(super) fn node(node: Node, kind: Type, loc: Loc, at: &Pointer) -> Self {
        Self {
            kind,
            loc,
            at: at.clone(),
            form: Form::Node(node),
        }
    }

    /// The constant, when the expression is one.
    pub(super) fn constant_value(&self) -> Option<&Json> {
        match &self.form {
            Form::Const(value, _) => Some(value),
            _ => None,
        }
    }

    /// Whether the expression is the constant `true`.
    pub(super) fn is_true(&self) -> bool {
        self.constant_value() == Some(&Json::Bool(true))
    }

    /// Whether the expression is the constant `false`.
    pub(super) fn is_false(&self) -> bool {
        self.constant_value() == Some(&Json::Bool(false))
    }

    /// The same expression with the static type `kind`, whose layout must equal the current one.
    pub(super) fn retyped(self, kind: Type) -> Self {
        Self { kind, ..self }
    }

    /// The expression as the evaluator runs it.
    pub(super) fn lower(self) -> Expr {
        let node = match self.form {
            Form::Const(_, value) => Node::Const(value),
            Form::Node(node) => node,
            Form::Members { members, check } => {
                let record = Node::Record(
                    members
                        .into_iter()
                        .map(|(_, member)| member.lower())
                        .collect(),
                );
                if check {
                    let inner = Expr {
                        node: record,
                        kind: Arc::clone(&self.kind),
                        loc: self.loc,
                    };
                    Node::Check(Box::new(inner))
                } else {
                    record
                }
            }
        };
        Expr {
            node,
            kind: self.kind,
            loc: self.loc,
        }
    }
}
