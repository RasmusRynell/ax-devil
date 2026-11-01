//! Templates: their parameter declarations, the acyclic call graph and one specialized program per set of constant
//! call-site inputs.

use std::collections::HashMap;
use std::rc::Rc;
use std::sync::Arc;

use super::expr::{Fault, Shared};
use super::program::Roots;
use crate::FeatureSet;
use crate::definitions::{MAX_TEMPLATE_DEPTH, Type};
use crate::diagnostics::{Pointer, Problem, codes};
use crate::document::Json;
use crate::ir::{Program, TypeName, ValueType};
use crate::validate::literal;
use crate::validate::structure::{object, parameter};

/// One compiled program with what its callers need to know.
pub(super) struct Compiled {
    pub(super) program: Arc<Program>,
    /// Steps it runs with every call expanded, capped just past the limit.
    pub(super) expanded: usize,
    /// Its template nesting, itself included.
    pub(super) depth: usize,
    /// Every feature tag on what it can draw.
    pub(super) features: FeatureSet,
}

/// A template's declaration.
pub(super) struct Template<'d> {
    pub(super) name: &'d str,
    pub(super) definition: &'d [(String, Json)],
    pub(super) at: Pointer,
    /// The `parameters` record, fields sorted by name.
    pub(super) parameters: Type,
    /// Checked defaults by parameter name.
    pub(super) defaults: Vec<(String, Json)>,
    /// Whether every parameter declaration is valid.
    pub(super) valid: bool,
}

/// One catalog's compilation: templates are compiled on first use and shared.
pub(super) struct CatalogCompiler<'d> {
    pub(super) shared: Shared,
    pub(super) templates: Vec<Template<'d>>,
    generic: Vec<Option<Result<Rc<Compiled>, ()>>>,
    active: Vec<bool>,
    specialized: HashMap<(usize, String), Result<Rc<Compiled>, ()>>,
}

impl<'d> CatalogCompiler<'d> {
    /// Reads every template declaration of `templates`, recording problems with parameters.
    pub(super) fn new(mut shared: Shared, templates: &'d [(String, Json)], at: &Pointer) -> Self {
        let declared = templates
            .iter()
            .map(|(name, raw)| declare(&mut shared, name, raw, &at.key(name)))
            .collect::<Vec<_>>();
        Self {
            generic: (0..declared.len()).map(|_| None).collect(),
            active: vec![false; declared.len()],
            templates: declared,
            specialized: HashMap::new(),
            shared,
        }
    }

    /// The index of the template called `name`.
    pub(super) fn find(&self, name: &str) -> Option<usize> {
        self.templates
            .iter()
            .position(|template| template.name == name)
    }

    /// Compiles every template once, so problems in templates no recipe calls are found too.
    pub(super) fn compile_all(&mut self) {
        for index in 0..self.templates.len() {
            let at = self.templates[index].at.clone();
            if let Err(fault) = self.template(index, &at) {
                fault.record(&mut self.shared.problems);
            }
        }
    }

    /// Template `index` compiled for any inputs, rejecting recursion; `at` is the call site.
    pub(super) fn template(&mut self, index: usize, at: &Pointer) -> Result<Rc<Compiled>, Fault> {
        if self.active[index] {
            let template = &self.templates[index];
            let message = format!("Recursive template call: {}.", template.name);
            return Err(Problem::new(codes::TEMPLATE_CYCLE, message)
                .at(&template.at)
                .into());
        }
        if let Some(done) = &self.generic[index] {
            return done.clone().map_err(|()| Fault::Reported);
        }
        if self.active.iter().filter(|active| **active).count() >= MAX_TEMPLATE_DEPTH {
            return Err(
                Problem::new(codes::EXECUTION_LIMIT, "Template nesting limit exceeded.")
                    .at(at)
                    .into(),
            );
        }
        if !self.templates[index].valid {
            self.generic[index] = Some(Err(()));
            return Err(Fault::Reported);
        }
        self.active[index] = true;
        let compiled = self.compile_template(index, HashMap::new());
        self.active[index] = false;
        let stored = self.store(compiled);
        self.generic[index] = Some(stored.clone());
        stored.map_err(|()| Fault::Reported)
    }

    /// Keeps a compiled template, or records why it failed. A template with any problem, its own or a template's it
    /// calls, counts as failed, so its callers neither specialize it nor repeat the problem.
    fn store(&mut self, compiled: (Result<Compiled, Fault>, bool)) -> Result<Rc<Compiled>, ()> {
        match compiled {
            (Ok(compiled), true) => Ok(Rc::new(compiled)),
            (Ok(_), false) => Err(()),
            (Err(fault), _) => {
                fault.record(&mut self.shared.problems);
                Err(())
            }
        }
    }

    /// Template `index` specialized for the constant inputs `constants`; `at` is the call site.
    pub(super) fn specialize(
        &mut self,
        index: usize,
        constants: Vec<(String, Json)>,
        at: &Pointer,
    ) -> Result<Rc<Compiled>, Fault> {
        if constants.is_empty() {
            return self.template(index, at);
        }
        let key = (index, constant_key(&constants));
        if let Some(done) = self.specialized.get(&key) {
            return done.clone().map_err(|()| Fault::Reported);
        }
        let compiled = self.compile_template(index, constants.into_iter().collect());
        let stored = self.store(compiled);
        self.specialized.insert(key, stored.clone());
        stored.map_err(|()| Fault::Reported)
    }

    /// Compiles template `index` with the constant inputs `statics`, telling whether no problem was found on the way.
    fn compile_template(
        &mut self,
        index: usize,
        statics: HashMap<String, Json>,
    ) -> (Result<Compiled, Fault>, bool) {
        let template = &self.templates[index];
        let (definition, at) = (template.definition, template.at.clone());
        let roots = Roots {
            parameters: Some(Arc::clone(&template.parameters)),
            scene: None,
            bindings: None,
        };
        let before = self.shared.problems.len();
        let built = self.program(definition, roots, &at, true, statics);
        let clean = self.shared.problems.len() == before;
        let compiled = built.map(|built| Compiled {
            program: Arc::new(built.program),
            expanded: built.expanded,
            depth: built.depth,
            features: built.features,
        });
        (compiled, clean)
    }
}

/// Reads one template's parameter declarations into its `parameters` record and checked defaults.
fn declare<'d>(shared: &mut Shared, name: &'d str, raw: &'d Json, at: &Pointer) -> Template<'d> {
    let empty = ValueType::scalar(TypeName::Parameters);
    let mut template = Template {
        name,
        definition: &[],
        at: at.clone(),
        parameters: Arc::new(empty.clone()),
        defaults: Vec::new(),
        valid: false,
    };
    let definition = match object(raw, at) {
        Ok(definition) => definition,
        Err(problem) => {
            shared.problems.push(problem);
            return template;
        }
    };
    template.definition = definition;
    let parameters_at = at.key("parameters");
    let declared = match definition.iter().find(|(key, _)| key == "parameters") {
        None => Ok(&[][..]),
        Some((_, parameters)) => object(parameters, &parameters_at),
    };
    let declared = match declared {
        Ok(declared) => declared,
        Err(problem) => {
            shared.problems.push(problem);
            return template;
        }
    };
    let mut sorted: Vec<&(String, Json)> = declared.iter().collect();
    sorted.sort_by(|left, right| left.0.cmp(&right.0));
    let mut fields = Vec::with_capacity(sorted.len());
    let mut valid = true;
    for (key, raw) in sorted {
        let at = parameters_at.key(key);
        let declared = object(raw, &at).and_then(|declaration| {
            let kind = parameter(declaration, &at)?;
            let default = declaration
                .iter()
                .find(|(name, _)| name == "default")
                .map(|(_, value)| value);
            if let Some(default) = default {
                literal(default, &kind, key)
                    .map_err(|message| Problem::value(message).at(&at.key("default")))?;
            }
            Ok((kind, default))
        });
        match declared {
            Ok((kind, default)) => {
                if let Some(default) = default {
                    template.defaults.push((key.clone(), default.clone()));
                }
                fields.push((Arc::from(key.as_str()), kind));
            }
            Err(problem) => {
                shared.problems.push(problem);
                valid = false;
            }
        }
    }
    template.parameters = Arc::new(ValueType {
        fields: fields.into(),
        ..empty
    });
    template.valid = valid;
    template
}

/// A key telling constant inputs apart exactly, signed zeros and number kinds included.
fn constant_key(constants: &[(String, Json)]) -> String {
    fn write(value: &Json, out: &mut String) {
        match value {
            Json::Null => out.push('n'),
            Json::Bool(flag) => out.push(if *flag { 't' } else { 'f' }),
            Json::Int(number) => out.push_str(&format!("i{number};")),
            Json::Float(number) => out.push_str(&format!("d{:x};", number.to_bits())),
            Json::Str(text) => out.push_str(&format!("s{}:{text}", text.len())),
            Json::Array(items) => {
                out.push('[');
                items.iter().for_each(|item| write(item, out));
                out.push(']');
            }
            Json::Object(members) => {
                let mut sorted: Vec<&(String, Json)> = members.iter().collect();
                sorted.sort_by(|left, right| left.0.cmp(&right.0));
                out.push('{');
                for (key, member) in sorted {
                    out.push_str(&format!("{}:{key}", key.len()));
                    write(member, out);
                }
                out.push('}');
            }
        }
    }
    let mut sorted: Vec<&(String, Json)> = constants.iter().collect();
    sorted.sort_by(|left, right| left.0.cmp(&right.0));
    let mut out = String::new();
    for (key, value) in sorted {
        out.push_str(&format!("{}:{key}", key.len()));
        write(value, &mut out);
    }
    out
}
