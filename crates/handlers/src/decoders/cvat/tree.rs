//! XML elements as [`Value`]s, built while the file streams past.
//!
//! An element is a map: `@attributes` first (when it has any), then each child tag in first-occurrence order (a
//! repeated tag becomes a list at the position of its first occurrence), then `#text`, the stripped text before the
//! first child. An element with only text is that text, and an empty one is an empty map.

use std::borrow::Cow;

use quick_xml::events::BytesStart;
use scene::{Fields, MAX_VALUE_DEPTH, Name, Value};

/// How deeply elements may nest in one tree. Every level can take a map and a list, and the decoder wraps trees in a
/// few more levels, so this stays well below [`MAX_VALUE_DEPTH`].
pub(super) const MAX_ELEMENT_DEPTH: usize = (MAX_VALUE_DEPTH - 8) / 2;

/// A tag or attribute name, static when the name is one CVAT files use all the time.
pub(super) fn name(text: &str) -> Name {
    let known: &'static str = match text {
        "box" => "box",
        "polygon" => "polygon",
        "polyline" => "polyline",
        "points" => "points",
        "ellipse" => "ellipse",
        "mask" => "mask",
        "skeleton" => "skeleton",
        "attribute" => "attribute",
        "track" => "track",
        "image" => "image",
        "id" => "id",
        "name" => "name",
        "label" => "label",
        "frame" => "frame",
        "outside" => "outside",
        "occluded" => "occluded",
        "keyframe" => "keyframe",
        "z_order" => "z_order",
        "group_id" => "group_id",
        "source" => "source",
        "xtl" => "xtl",
        "ytl" => "ytl",
        "xbr" => "xbr",
        "ybr" => "ybr",
        "width" => "width",
        "height" => "height",
        "subset" => "subset",
        "rotation" => "rotation",
        "origin" => "origin",
        _ => return Cow::Owned(text.to_owned()),
    };
    Cow::Borrowed(known)
}

/// The attributes of `element` as text, in document order.
pub(super) fn attributes(element: &BytesStart<'_>) -> Result<Fields, String> {
    let raw = element.attributes_raw();
    if raw.is_empty() {
        return Ok(Fields::new());
    }
    let mut fields = Fields::with_capacity(memchr::memchr_iter(b'=', raw.as_bytes()).count());
    for attribute in element.attributes() {
        let attribute = attribute.map_err(|error| format!("invalid attribute: {error}"))?;
        let value = attribute
            .normalized_value(quick_xml::XmlVersion::Implicit1_0)
            .map_err(|error| format!("invalid attribute value: {error}"))?;
        fields.push((
            name(attribute.key.as_ref()),
            Value::Str(Name::Owned(value.into_owned())),
        ));
    }
    Ok(fields)
}

/// One element being read: its parts are filled in as the file streams past.
pub(super) struct Node {
    tag: Name,
    attributes: Fields,
    children: Fields,
    text: String,
    has_child: bool,
}

impl Node {
    /// An element with these attributes and nothing inside yet.
    pub(super) fn new(tag: Name, attributes: Fields) -> Self {
        Self {
            tag,
            attributes,
            children: Fields::new(),
            text: String::new(),
            has_child: false,
        }
    }

    /// The element's tag.
    pub(super) fn tag(&self) -> &Name {
        &self.tag
    }

    /// The element's attributes.
    pub(super) fn attributes(&self) -> &Fields {
        &self.attributes
    }

    /// Adds text found inside the element; only the text before the first child counts.
    fn text(&mut self, text: &str) {
        if !self.has_child && !(self.text.is_empty() && text.trim().is_empty()) {
            self.text.push_str(text);
        }
    }

    /// Adds a finished child: a tag seen again turns the first value into a list.
    pub(super) fn child(&mut self, tag: Name, value: Value) {
        self.has_child = true;
        let Some(position) = self
            .children
            .iter()
            .position(|(existing, _)| *existing == tag)
        else {
            self.children.push((tag, value));
            return;
        };
        match &mut self.children[position].1 {
            Value::List(items) => items.push(value),
            first => {
                let earlier = std::mem::replace(first, Value::Null);
                *first = Value::List(vec![earlier, value]);
            }
        }
    }

    /// The element as a [`Value`].
    pub(super) fn into_value(self) -> Value {
        let text = self.text.trim();
        let text = if text.len() == self.text.len() {
            self.text
        } else {
            text.to_owned()
        };
        if self.attributes.is_empty() && self.children.is_empty() {
            return if text.is_empty() {
                Value::Map(Fields::new())
            } else {
                Value::Str(Name::Owned(text))
            };
        }
        let mut fields = self.children;
        if !self.attributes.is_empty() {
            fields.insert(0, ("@attributes".into(), Value::Map(self.attributes)));
        }
        if !text.is_empty() {
            fields.push(("#text".into(), Value::Str(Name::Owned(text))));
        }
        Value::Map(fields)
    }
}

/// Builds the [`Value`] of one element and everything inside it.
pub(super) struct TreeBuilder {
    open: Vec<Node>,
}

impl TreeBuilder {
    /// A builder whose outermost element has just started.
    pub(super) fn started(tag: Name, attributes: Fields) -> Result<Self, String> {
        Ok(Self {
            open: vec![Node::new(tag, attributes)],
        })
    }

    /// An element starts inside the open one.
    pub(super) fn start(&mut self, tag: Name, attributes: Fields) -> Result<(), String> {
        if self.open.len() >= MAX_ELEMENT_DEPTH {
            return Err(format!(
                "elements nest deeper than {MAX_ELEMENT_DEPTH} levels"
            ));
        }
        self.open.push(Node::new(tag, attributes));
        Ok(())
    }

    /// Text inside the open element.
    pub(super) fn text(&mut self, text: &str) {
        if let Some(node) = self.open.last_mut() {
            node.text(text);
        }
    }

    /// The open element ends. A nested one joins its parent; the outermost one is returned finished.
    pub(super) fn end(&mut self) -> Option<(Name, Value)> {
        let mut node = self.open.pop()?;
        let tag = std::mem::take(&mut node.tag);
        let value = node.into_value();
        match self.open.last_mut() {
            Some(parent) => {
                parent.child(tag, value);
                None
            }
            None => Some((tag, value)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build(xml: &str) -> Value {
        let mut reader = quick_xml::Reader::from_str(xml);
        reader.config_mut().expand_empty_elements = true;
        let mut builder: Option<TreeBuilder> = None;
        loop {
            match reader.read_event().expect("valid XML") {
                quick_xml::events::Event::Start(element) => {
                    let (tag, attrs) = (
                        name(element.name().as_ref()),
                        attributes(&element).expect("attributes"),
                    );
                    match &mut builder {
                        Some(builder) => builder.start(tag, attrs).expect("depth"),
                        None => builder = Some(TreeBuilder::started(tag, attrs).expect("start")),
                    }
                }
                quick_xml::events::Event::Text(text) => {
                    if let Some(builder) = &mut builder {
                        builder.text(&text.xml10_content());
                    }
                }
                quick_xml::events::Event::End(_) => {
                    if let Some((_, value)) = builder.as_mut().and_then(TreeBuilder::end) {
                        return value;
                    }
                }
                _ => {}
            }
        }
    }

    fn keys(value: &Value) -> Vec<&str> {
        value.as_map().map_or_else(Vec::new, |fields| {
            fields.iter().map(|(key, _)| key.as_ref()).collect()
        })
    }

    #[test]
    fn elements_become_maps_with_attributes_first_and_repeated_tags_listed() {
        let value = build(r#"<a k="v"><b>1</b><c/><b u="x">2</b>head<d>tail</d>tail</a>"#);

        assert_eq!(keys(&value), ["@attributes", "b", "c", "d"]);
        let fields = value.as_map().expect("a map");
        let repeated = fields[1].1.as_list().expect("b repeats");
        assert_eq!(repeated[0].as_str(), Some("1"));
        assert_eq!(keys(&repeated[1]), ["@attributes", "#text"]);
        assert_eq!(fields[2].1, Value::Map(Fields::new()));
        assert_eq!(fields[3].1.as_str(), Some("tail"));
    }

    #[test]
    fn text_before_the_first_child_is_kept_stripped_and_later_text_is_not() {
        let value = build("<a>\n  hello <b/> after </a>");
        assert_eq!(keys(&value), ["b", "#text"]);
        assert_eq!(value.as_map().map(|f| f[1].1.as_str()), Some(Some("hello")));
    }

    #[test]
    fn nesting_past_the_limit_is_refused() {
        let mut builder = TreeBuilder::started("a".into(), Fields::new()).expect("start");
        let deeper =
            (1..MAX_ELEMENT_DEPTH).try_for_each(|_| builder.start("a".into(), Fields::new()));
        assert!(deeper.is_ok());
        assert!(builder.start("a".into(), Fields::new()).is_err());
    }
}
