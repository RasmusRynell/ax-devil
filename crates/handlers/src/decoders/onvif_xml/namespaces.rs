//! The namespace scopes of one XML document, resolved as Python's ElementTree does.
//!
//! Each declaration is normalised once and interned, so an element or attribute names its namespace by an index and
//! the work per element does not grow with the length of a namespace.

use std::{
    borrow::Cow,
    collections::{HashMap, HashSet},
    rc::Rc,
};

use quick_xml::{
    XmlVersion,
    events::{BytesStart, attributes::Attribute},
    name::PrefixDeclaration,
};

/// The namespaces XML reserves: only the `xml` prefix names the first, and no prefix names the second.
const XML_NAMESPACE: &str = "http://www.w3.org/XML/1998/namespace";
const XMLNS_NAMESPACE: &str = "http://www.w3.org/2000/xmlns/";

/// Where `XML_NAMESPACE` sits in [`Namespaces::names`].
const XML: usize = 0;

/// The namespace declarations in scope while a document is read.
pub(super) struct Namespaces {
    /// Every distinct namespace declared so far, by its index in `names`.
    ids: HashMap<Rc<str>, usize>,
    names: Vec<Rc<str>>,
    /// The declarations of each prefix in scope, innermost last; the default namespace is the prefix `""`, and `None`
    /// undeclares it.
    bound: HashMap<String, Vec<Option<usize>>>,
    /// The prefixes the open elements declared, outermost first, and how many each element holds.
    declared: Vec<String>,
    counts: Vec<usize>,
}

impl Namespaces {
    pub(super) fn new() -> Self {
        let name: Rc<str> = Rc::from(XML_NAMESPACE);
        Self {
            ids: HashMap::from([(name.clone(), XML)]),
            names: vec![name],
            bound: HashMap::new(),
            declared: Vec::new(),
            counts: Vec::new(),
        }
    }

    /// Opens `element`: takes its declarations into scope and gives the namespace of its name. Python rejects an
    /// element with an attribute value that has a bad reference or a character XML 1.0 forbids, an unbound prefix,
    /// two attributes of one namespace and name, or a declaration that is empty (for a prefix) or reserved, even when
    /// nothing reads them. Each attribute value is scanned once.
    pub(super) fn enter(&mut self, element: &BytesStart<'_>) -> Result<Option<Rc<str>>, String> {
        self.counts.push(0);
        let attributes = || element.attributes();
        for attribute in attributes() {
            let attribute = attribute.map_err(|error| format!("invalid attribute: {error}"))?;
            let value = checked_value(&attribute)?;
            if let Some(declaration) = attribute.key.as_namespace_binding() {
                self.declare(declaration, &value)?;
            }
        }
        let mut seen = HashSet::new();
        for attribute in attributes() {
            let attribute = attribute.map_err(|error| format!("invalid attribute: {error}"))?;
            if attribute.key.as_namespace_binding().is_some() {
                continue;
            }
            let (local, prefix) = attribute.key.decompose();
            if let Some(prefix) = prefix {
                let id = self.lookup(prefix.into_inner())?;
                if !seen.insert((id, local.into_inner())) {
                    return Err(format!(
                        "invalid XML: duplicate attribute {:?}",
                        local.into_inner()
                    ));
                }
            }
        }
        let prefix = element
            .name()
            .prefix()
            .map_or("", |prefix| prefix.into_inner());
        Ok(self.lookup(prefix)?.map(|id| self.names[id].clone()))
    }

    /// Closes the innermost open element, taking its declarations out of scope.
    pub(super) fn leave(&mut self) {
        for _ in 0..self.counts.pop().unwrap_or(0) {
            if let Some(prefix) = self.declared.pop()
                && let Some(stack) = self.bound.get_mut(&prefix)
            {
                stack.pop();
            }
        }
    }

    fn declare(&mut self, declaration: PrefixDeclaration<'_>, value: &str) -> Result<(), String> {
        let prefix = match declaration {
            PrefixDeclaration::Default => "",
            PrefixDeclaration::Named(prefix) => {
                if prefix.is_empty() || value.is_empty() {
                    return Err("invalid XML: a prefix cannot be empty or unbound".to_string());
                }
                if prefix == "xmlns" {
                    return Err("invalid XML: the prefix xmlns cannot be declared".to_string());
                }
                prefix
            }
        };
        if value == XMLNS_NAMESPACE || (value == XML_NAMESPACE) != (prefix == "xml") {
            return Err(format!(
                "invalid XML: {value:?} cannot be bound to prefix {prefix:?}"
            ));
        }
        if prefix == "xml" {
            return Ok(());
        }
        let id = (!value.is_empty()).then(|| self.intern(value));
        self.bound.entry(prefix.to_owned()).or_default().push(id);
        self.declared.push(prefix.to_owned());
        if let Some(count) = self.counts.last_mut() {
            *count += 1;
        }
        Ok(())
    }

    fn intern(&mut self, namespace: &str) -> usize {
        if let Some(id) = self.ids.get(namespace) {
            return *id;
        }
        let name: Rc<str> = Rc::from(namespace);
        self.names.push(name.clone());
        self.ids.insert(name, self.names.len() - 1);
        self.names.len() - 1
    }

    /// The namespace `prefix` names, where `""` is the default namespace.
    fn lookup(&self, prefix: &str) -> Result<Option<usize>, String> {
        if prefix == "xml" {
            return Ok(Some(XML));
        }
        match self.bound.get(prefix).and_then(|stack| stack.last()) {
            Some(id) => Ok(*id),
            None if prefix.is_empty() => Ok(None),
            None => Err(format!("invalid XML: unbound prefix {prefix:?}")),
        }
    }
}

/// The value of `attribute` as a parser reads it: references expanded, tab and newline as spaces, and only XML 1.0
/// characters.
fn checked_value<'a>(attribute: &Attribute<'a>) -> Result<Cow<'a, str>, String> {
    let value = attribute
        .normalized_value(XmlVersion::Implicit1_0)
        .map_err(|error| format!("invalid XML: {error}"))?;
    match value.chars().find(|c| !is_xml_char(*c)) {
        Some(character) => Err(format!(
            "invalid XML: character {:#x} is not allowed",
            u32::from(character)
        )),
        None => Ok(value),
    }
}

/// Whether XML 1.0 allows `character`; Rust's `char` already excludes the surrogates.
pub(super) fn is_xml_char(character: char) -> bool {
    matches!(character, '\t' | '\n' | '\r' | ' '..='\u{FFFD}' | '\u{10000}'..)
}
