//! CVAT annotation XML: `track` and `image` elements of box and polygon shapes, one Scene per frame.
//!
//! One streaming pass keeps no document tree: only the values the Scenes and the summary expose are built, and the
//! frames wait for the end of the file, because the image size and a track's own attributes may come after the
//! shapes. Only the `meta/task` layout is read, not `meta/job`.

mod frames;
mod meta;
mod shape;
mod tree;

use std::collections::{BTreeMap, BTreeSet, HashMap};

use foundation::{CancelToken, Interrupt};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Input, OnEqualKey, SceneSink,
    SkippedRecords, Spec, Values,
};
use quick_xml::{
    Reader,
    events::{BytesStart, Event},
};
use scene::{ClassType, Coverage, EntityId, Fields, MatchBasis, Name, Value};

use frames::{FrameAcc, Pending, filter_policy};
use meta::Essentials;
use shape::{Draw, FrameRef, ShapeHead, attr, classification_attributes, int};
use tree::{Node, TreeBuilder};

/// Events between two checks of the cancel token.
const CANCEL_EVERY: usize = 4096;

/// Reads CVAT annotation XML.
pub struct CvatDecoder {
    spec: Spec,
}

impl CvatDecoder {
    pub(crate) fn new() -> Self {
        Self {
            spec: Spec {
                id: "CVAT".to_owned(),
                plugin: "axis-cvat".to_owned(),
                name: "CVAT".to_owned(),
                extensions: vec![".xml".to_owned()],
                description: None,
                framing: Framing::Whole,
                bases: vec![MatchBasis::Frame],
                default_basis: MatchBasis::Frame,
                options: Form::default(),
                version: 3,
            },
        }
    }
}

impl Decoder for CvatDecoder {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let input = input.next(cancel)?.map_or(&[][..], |record| record.bytes);
        let mut parser = Parser::default();
        let mut reader = Reader::from_reader(input);
        reader.config_mut().expand_empty_elements = true;
        let fail = |reader: &Reader<&[u8]>, message: String| {
            let (line, column) = location(input, reader.buffer_position());
            Interrupt::Failed(DecodeError(format!(
                "Invalid CVAT XML at line {line}, column {column}: {message}"
            )))
        };
        let mut events = 0_usize;
        loop {
            if events.is_multiple_of(CANCEL_EVERY) {
                cancel.check()?;
            }
            events += 1;
            let event = reader
                .read_event()
                .map_err(|error| fail(&reader, error.to_string()))?;
            let handled = match event {
                Event::Start(element) => parser.start(&element),
                Event::End(_) => parser.end(),
                Event::Text(text) => {
                    parser.text(&text.xml10_content());
                    Ok(())
                }
                Event::CData(text) => {
                    parser.text(&text.xml10_content());
                    Ok(())
                }
                Event::GeneralRef(reference) => {
                    match reference.resolve_char_ref() {
                        Ok(Some(character)) => parser.text(character.encode_utf8(&mut [0; 4])),
                        _ => parser.text(match &*reference {
                            "lt" => "<",
                            "gt" => ">",
                            "amp" => "&",
                            "quot" => "\"",
                            "apos" => "'",
                            _ => "",
                        }),
                    }
                    Ok(())
                }
                Event::Eof => break,
                _ => Ok(()),
            };
            handled.map_err(|message| fail(&reader, message))?;
        }
        parser.finish(sink, cancel)
    }
}

/// The line and column (from 1) of byte `offset` in `input`.
fn location(input: &[u8], offset: u64) -> (usize, usize) {
    let before =
        &input[..usize::try_from(offset).map_or(input.len(), |offset| offset.min(input.len()))];
    let line = memchr::memchr_iter(b'\n', before).count() + 1;
    let column = before.len() - memchr::memrchr(b'\n', before).map_or(0, |newline| newline + 1) + 1;
    (line, column)
}

/// What a direct child of the root is being read as.
enum Mode {
    Idle,
    /// A subtree that becomes a value: the `meta` element, or any other element the document value keeps.
    Tree(Slot, TreeBuilder),
    Container(Box<Container>),
}

#[derive(Clone, Copy)]
enum Slot {
    Meta,
    Document,
}

enum Kind {
    /// The entity every shape of the track belongs to; `None` when the track id is not a whole number, and each
    /// shape then gets an entity of its own like an image shape.
    Track(Option<EntityId>),
    /// The image's frame, if it names one.
    Image(Option<u64>),
}

/// A shape element read in full, waiting for its container to end.
struct Shape {
    head: ShapeHead,
    tag: Name,
    value: Value,
}

/// The element being read inside a container.
struct Child {
    builder: TreeBuilder,
    /// `None` for an `attribute` element.
    head: Option<ShapeHead>,
    /// Whether the container's context includes the element.
    in_context: bool,
}

/// A `track` or `image` element. Its shapes are held until it ends, because its attributes can come after them.
struct Container {
    kind: Kind,
    context: Node,
    shapes: Vec<Shape>,
    child: Option<Child>,
}

impl Container {
    fn new(tag: Name, attributes: Fields) -> Self {
        let kind = if tag == "track" {
            Kind::Track(
                attr(&attributes, "id")
                    .and_then(int)
                    .and_then(|id| EntityId::new(format!("cvat_track_{id}")).ok()),
            )
        } else {
            let frame = attr(&attributes, "id").or_else(|| attr(&attributes, "frame"));
            Kind::Image(frame.and_then(|text| text.trim().parse().ok()))
        };
        Self {
            kind,
            context: Node::new(tag, attributes),
            shapes: Vec::new(),
            child: None,
        }
    }

    fn start(&mut self, tag: Name, attributes: Fields) -> Result<(), String> {
        if let Some(child) = &mut self.child {
            return child.builder.start(tag, attributes);
        }
        let is_attribute = tag == "attribute";
        let head =
            (!is_attribute).then(|| ShapeHead::read(&tag, &attributes, self.context.attributes()));
        let in_context = is_attribute
            || (matches!(self.kind, Kind::Track(_)) && attr(&attributes, "frame").is_none());
        self.child = Some(Child {
            builder: TreeBuilder::started(tag, attributes)?,
            head,
            in_context,
        });
        Ok(())
    }

    fn end(&mut self) {
        let Some((tag, value)) = self.child.as_mut().and_then(|child| child.builder.end()) else {
            return;
        };
        let Some(child) = self.child.take() else {
            return;
        };
        match child.head {
            None => self.context.child(tag, value),
            Some(head) => {
                if child.in_context {
                    self.context.child(tag.clone(), value.clone());
                }
                self.shapes.push(Shape { head, tag, value });
            }
        }
    }
}

/// The state of the single pass.
struct Parser {
    depth: usize,
    /// The root element: its attributes and every child that is not `meta`, `track` or `image`.
    document: Option<Node>,
    meta: Option<Value>,
    mode: Mode,
    frames: BTreeMap<u64, FrameAcc>,
    labels: BTreeSet<ClassType>,
    skipped: SkippedRecords,
    shapes: usize,
    /// Shapes of images and of tracks without a whole-number id that reached the box or polygon builder; numbers
    /// their entities.
    synthetic_ids: u64,
}

impl Default for Parser {
    fn default() -> Self {
        Self {
            depth: 0,
            document: None,
            meta: None,
            mode: Mode::Idle,
            frames: BTreeMap::new(),
            labels: BTreeSet::new(),
            skipped: SkippedRecords::new("shapes", "Those shapes are not drawn."),
            shapes: 0,
            synthetic_ids: 0,
        }
    }
}

impl Parser {
    fn start(&mut self, element: &BytesStart<'_>) -> Result<(), String> {
        let tag = tree::name(element.name().as_ref());
        let attributes = tree::attributes(element)?;
        match (self.depth, &mut self.mode) {
            (0, _) => {
                if self.document.is_some() {
                    return Err("content after the root element".to_owned());
                }
                self.document = Some(Node::new(tag, attributes));
            }
            (1, mode) => {
                *mode = match &*tag {
                    "track" | "image" => Mode::Container(Box::new(Container::new(tag, attributes))),
                    "meta" => Mode::Tree(Slot::Meta, TreeBuilder::started(tag, attributes)?),
                    _ => Mode::Tree(Slot::Document, TreeBuilder::started(tag, attributes)?),
                }
            }
            (_, Mode::Tree(_, builder)) => builder.start(tag, attributes)?,
            (_, Mode::Container(container)) => container.start(tag, attributes)?,
            (_, Mode::Idle) => {}
        }
        self.depth += 1;
        Ok(())
    }

    fn text(&mut self, text: &str) {
        match &mut self.mode {
            Mode::Tree(_, builder) => builder.text(text),
            Mode::Container(container) => {
                if let Some(child) = &mut container.child {
                    child.builder.text(text);
                }
            }
            Mode::Idle => {}
        }
    }

    fn end(&mut self) -> Result<(), String> {
        self.depth = self
            .depth
            .checked_sub(1)
            .ok_or("a closing tag without an opening one")?;
        match self.depth {
            0 => {}
            1 => match std::mem::replace(&mut self.mode, Mode::Idle) {
                Mode::Tree(slot, mut builder) => {
                    if let Some((tag, value)) = builder.end() {
                        match (slot, &mut self.document) {
                            (Slot::Meta, _) => {
                                self.meta.get_or_insert(value);
                            }
                            (Slot::Document, Some(document)) => document.child(tag, value),
                            (Slot::Document, None) => {}
                        }
                    }
                }
                Mode::Container(container) => self.flush(*container)?,
                Mode::Idle => {}
            },
            _ => match &mut self.mode {
                Mode::Tree(_, builder) => {
                    builder.end();
                }
                Mode::Container(container) => container.end(),
                Mode::Idle => {}
            },
        }
        Ok(())
    }

    /// Files the shapes of a finished container under their frames.
    fn flush(&mut self, container: Container) -> Result<(), String> {
        let Container {
            kind,
            context,
            shapes,
            ..
        } = container;
        let tag = context.tag().clone();
        let id_text = attr(context.attributes(), "id").unwrap_or("").to_owned();
        let size = ["width", "height"].map(|name| {
            attr(context.attributes(), name)
                .and_then(int)
                .filter(|&n| n != 0)
                .unwrap_or(0)
        });
        let own_frame = match kind {
            Kind::Image(frame) => frame,
            Kind::Track(_) => None,
        };
        let is_image = matches!(kind, Kind::Image(_));
        let context = context.into_value();
        if let Some(frame) = own_frame {
            self.frames
                .entry(frame)
                .or_default()
                .set_image(context.clone(), size);
        }
        for Shape {
            head,
            tag: shape_tag,
            value,
        } in shapes
        {
            self.shapes += 1;
            let frame = match (&kind, head.frame) {
                (_, FrameRef::At(frame)) => Ok(frame),
                (_, FrameRef::Invalid(text)) => {
                    Err(format!("frame {text:?} is not a frame number"))
                }
                (Kind::Image(Some(frame)), FrameRef::Absent) => Ok(*frame),
                (_, FrameRef::Absent) => Err("it has no frame".to_owned()),
            };
            let frame = match frame {
                Ok(frame) => frame,
                Err(problem) => {
                    self.skipped
                        .add(format!("<{shape_tag}> in <{tag} {id_text}>: {problem}"));
                    continue;
                }
            };
            let acc = self.frames.entry(frame).or_default();
            if is_image && own_frame != Some(frame) {
                acc.set_image(context.clone(), size);
            }
            let attributes = match &head.draw {
                Draw::Rendered(rendered) => classification_attributes(&context, &value, rendered),
                Draw::Skip | Draw::Rejected => Fields::new(),
            };
            let raw = Value::Map(vec![
                (tag.clone(), context.clone()),
                ("shape_type".into(), Value::Str(shape_tag)),
                ("shape".into(), value),
            ]);
            // A shape without a track entity, rendered or rejected, takes the next number.
            let number = match (&kind, &head.draw) {
                (Kind::Track(Some(_)), _) | (_, Draw::Skip) => None,
                _ => {
                    self.synthetic_ids += 1;
                    Some(self.synthetic_ids - 1)
                }
            };
            match head.draw {
                Draw::Skip | Draw::Rejected => acc.unrendered.push(raw),
                Draw::Rendered(rendered) => {
                    let entity = match (&kind, number) {
                        (Kind::Track(Some(id)), _) => id.clone(),
                        (_, number) => {
                            EntityId::new(format!("cvat_shape_{}", number.unwrap_or_default()))
                                .map_err(|error| error.to_string())?
                        }
                    };
                    if !self.labels.contains(&rendered.class) {
                        self.labels.insert(rendered.class.clone());
                    }
                    acc.shapes.push(Pending {
                        entity,
                        class: rendered.class,
                        drawn: rendered.drawn,
                        attributes,
                        raw,
                    });
                }
            }
        }
        Ok(())
    }

    /// Builds the Scenes and the summary once the whole file has been read.
    fn finish(
        mut self,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let failed = |message: String| Interrupt::Failed(DecodeError(message));
        if self.depth != 0 {
            return Err(failed(
                "Invalid CVAT XML: an element is left open".to_owned(),
            ));
        }
        let Some(document) = self.document.take() else {
            return Err(failed("Invalid CVAT XML: no element found".to_owned()));
        };
        let Some(meta) = self.meta.take() else {
            return Err(failed("No 'meta' tag found in CVAT XML file".to_owned()));
        };
        let mut essentials = Essentials::read(&meta).map_err(failed)?;
        let mut all_metadata = match meta {
            Value::Map(fields) => fields,
            other => vec![("text".into(), other)],
        };
        all_metadata.push(("@document".into(), document.into_value()));
        if essentials.size <= 0
            && let Some((&highest, _)) = self.frames.last_key_value()
        {
            essentials.size =
                i64::try_from(highest).map_or(i64::MAX, |highest| highest.saturating_add(1));
            essentials.stop_frame = i64::try_from(highest).unwrap_or(i64::MAX);
        }
        let fallback = [essentials.width, essentials.height];
        let mut index = HashMap::new();
        while let Some((frame, acc)) = self.frames.pop_first() {
            cancel.check()?;
            let scene = acc
                .into_scene(frame, fallback, &mut index)
                .map_err(failed)?;
            if sink.sample(scene, OnEqualKey::Replace).is_break() {
                break;
            }
        }
        Ok(DecodeSummary {
            warnings: self.skipped.warning(self.shapes).into_iter().collect(),
            filter: filter_policy(&self.labels),
            metadata: essentials.into_metadata(&self.labels, all_metadata),
            coverage: Coverage::Sparse,
        })
    }
}
