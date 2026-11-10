//! Axis ONVIF analytics metadata: one XML document per line, one frame per document.

mod namespaces;
mod shape;

use foundation::{CancelToken, Interrupt, MediaTime};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Header, Input, OnEqualKey, SceneSink, Spec,
    Values,
};
use namespaces::{Namespaces, is_xml_char};
use quick_xml::{
    Reader,
    events::{BytesStart, Event},
};
use scene::{
    ClassRule, Coverage, Entity, EntityId, Fields, FilterOption, FilterPolicy, MatchBasis, Name,
    Operation, Scene, SceneTime, TimeSlice,
};
use shape::{RawObject, Tag, attrs};

use crate::decoders::iso8601;

/// Filter id, label and the class it shows; `show_other` is added after them.
const FILTERS: [(&str, &str, &str); 13] = [
    ("show_unknown", "Unknown", "unknown"),
    ("show_humans", "Humans", "human"),
    ("show_heads", "Heads", "head"),
    ("show_vehicles", "Vehicles", "vehicle"),
    ("show_vehicles_other", "Vehicles (Other)", "vehicle_other"),
    ("show_cars", "Cars", "car"),
    ("show_buses", "Buses", "bus"),
    ("show_trucks", "Trucks", "truck"),
    ("show_bikes", "Bikes", "bike"),
    ("show_bicycles", "Bicycles", "bicycle"),
    ("show_animals", "Animals", "animal"),
    ("show_license_plates", "License Plates", "license_plate"),
    ("show_faces", "Faces", "face"),
];

/// Reads Axis ONVIF metadata XML, one document per line.
pub struct OnvifXmlDecoder {
    spec: Spec,
}

impl OnvifXmlDecoder {
    pub(crate) fn new() -> Self {
        Self {
            spec: Spec {
                id: "ONVIF_XML".to_owned(),
                plugin: "axis-onvif-xml".to_owned(),
                name: "Axis ONVIF XML".to_owned(),
                extensions: vec![".xml".to_owned()],
                description: None,
                framing: Framing::Records,
                bases: vec![MatchBasis::Frame, MatchBasis::Time],
                default_basis: MatchBasis::Frame,
                options: Form::default(),
                version: 5,
            },
        }
    }
}

impl Decoder for OnvifXmlDecoder {
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
        let filter = filter_policy();
        // A live source never ends, so it has its class filters from the start. A file is published once decoded, and
        // a header would publish it while it is still being read.
        if input.path().is_none() {
            sink.header(Header {
                filter: filter.clone(),
                coverage: Coverage::Sparse,
                metadata: Fields::new(),
            });
        }
        while let Some(record) = input.next(cancel)? {
            match decode_record(record.bytes) {
                Ok(Some(scene)) => {
                    if sink.sample(scene, OnEqualKey::Replace).is_break() {
                        break;
                    }
                }
                Ok(None) => {}
                Err(reason) => sink.skip(record.position, reason),
            }
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter,
            metadata: Fields::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// One trimmed, non-empty record. `Ok(None)` means the record has no overlay; `Err` carries the reason it is skipped.
fn decode_record(record: &[u8]) -> Result<Option<Scene>, String> {
    let mut document = Document::default();
    let mut namespaces = Namespaces::new();
    let mut reader = Reader::from_reader(record);
    loop {
        let event = reader
            .read_event()
            .map_err(|error| format!("invalid XML: {error}"))?;
        match event {
            Event::Start(element) => {
                let namespace = namespaces.enter(&element)?;
                document.open(&element, namespace.as_deref())?;
            }
            Event::Empty(element) => {
                let namespace = namespaces.enter(&element)?;
                document.open(&element, namespace.as_deref())?;
                document.close()?;
                namespaces.leave();
            }
            Event::End(_) => {
                document.close()?;
                namespaces.leave();
            }
            Event::Text(text) => document.text(&text.xml10_content()),
            Event::CData(text) => document.text(&text.xml10_content()),
            Event::GeneralRef(reference) => match reference.resolve_char_ref() {
                Ok(Some(character)) if is_xml_char(character) => {
                    document.text(character.encode_utf8(&mut [0; 4]));
                }
                Ok(None) => document.text(match &*reference {
                    "lt" => "<",
                    "gt" => ">",
                    "amp" => "&",
                    "quot" => "\"",
                    "apos" => "'",
                    other => return Err(format!("invalid XML: undefined entity {other:?}")),
                }),
                _ => return Err(format!("invalid XML: bad reference &{};", &*reference)),
            },
            Event::Eof => return document.finish(),
            _ => {}
        }
    }
}

/// The class filters of this decoder's content.
fn filter_policy() -> FilterPolicy {
    let mut options: Vec<FilterOption> = FILTERS
        .iter()
        .map(|&(id, label, class)| FilterOption {
            id: id.into(),
            label: label.into(),
            default_enabled: true,
            rule: ClassRule {
                types: vec![class.into()],
                include_empty: class == "unknown",
                exclude: false,
            },
        })
        .collect();
    options.push(FilterOption {
        id: "show_other".into(),
        label: "Other".into(),
        default_enabled: true,
        rule: ClassRule {
            types: FILTERS
                .iter()
                .map(|&(_, _, class)| Name::from(class))
                .collect(),
            include_empty: false,
            exclude: true,
        },
    });
    FilterPolicy {
        name: "onvif_xml",
        options,
    }
}

/// One XML document being read.
#[derive(Default)]
struct Document {
    stack: Vec<Tag>,
    root: Option<Tag>,
    frame: Option<Frame>,
    scene: Option<Scene>,
}

impl Document {
    fn open(&mut self, element: &BytesStart<'_>, namespace: Option<&str>) -> Result<(), String> {
        let local = element.local_name();
        let tag = Tag::resolve(namespace, local.as_ref());
        if self.stack.is_empty() {
            if self.root.is_some() {
                return Err("invalid XML: content after the root element".to_string());
            }
            self.root = Some(tag);
        }
        self.stack.push(tag);
        if let Some(frame) = &mut self.frame {
            frame.open(&self.stack, element)?;
        } else if tag == Tag::Frame && self.scene.is_none() {
            self.frame = Some(Frame::start(self.stack.len() - 1, element)?);
        }
        Ok(())
    }

    fn text(&mut self, text: &str) {
        if let Some(frame) = &mut self.frame {
            frame.text(text);
        }
    }

    fn close(&mut self) -> Result<(), String> {
        if let Some(frame) = &mut self.frame {
            if frame.depth + 1 == self.stack.len() {
                self.scene = self.frame.take().map(Frame::into_scene).transpose()?;
            } else {
                frame.close(&self.stack)?;
            }
        }
        self.stack.pop();
        Ok(())
    }

    /// The Scene of the document's frame, or `None` for a metadata stream without one.
    fn finish(self) -> Result<Option<Scene>, String> {
        if !self.stack.is_empty() {
            return Err("invalid XML: an element is left open".to_string());
        }
        match (self.scene, self.root) {
            (Some(scene), _) => Ok(Some(scene)),
            (None, None) => Err("invalid XML: no element found".to_string()),
            (None, Some(Tag::MetadataStream)) => Ok(None),
            (None, Some(_)) => Err("No <tt:Frame>/<tt:SampleFrame> elements found".to_string()),
        }
    }
}

/// The `Frame` element being read.
struct Frame {
    /// Where the `Frame` is in the element stack.
    depth: usize,
    time: MediaTime,
    entities: Vec<Entity>,
    /// Objects being read, with where each is in the element stack.
    objects: Vec<(usize, RawObject)>,
    renames: Vec<scene::Event>,
    deletes: Vec<scene::Event>,
    rename: [Option<EntityId>; 2],
}

impl Frame {
    fn start(depth: usize, element: &BytesStart<'_>) -> Result<Self, String> {
        let [utc_time] = attrs(element, ["UtcTime"])?;
        let time = utc_time
            .and_then(|text| iso8601::parse_timestamp(text.trim()))
            .ok_or("Frame lacks a valid UtcTime attribute")?;
        Ok(Self {
            depth,
            time,
            entities: Vec::new(),
            objects: Vec::new(),
            renames: Vec::new(),
            deletes: Vec::new(),
            rename: [None, None],
        })
    }

    /// The last element of `stack` just opened.
    fn open(&mut self, stack: &[Tag], element: &BytesStart<'_>) -> Result<(), String> {
        let at = stack.len() - 1;
        match stack {
            _ if stack[at] == Tag::Object => {
                let [id] = attrs(element, ["ObjectId"])?;
                self.objects.push((at, RawObject::new(id.as_deref())?));
            }
            [.., Tag::ObjectTree, Tag::Rename] => self.rename = [None, None],
            [.., Tag::Rename, side @ (Tag::From | Tag::To)] => {
                let [id] = attrs(element, ["ObjectId"])?;
                self.rename[usize::from(*side == Tag::To)] =
                    id.and_then(|id| EntityId::new(id).ok());
            }
            [.., Tag::ObjectTree, Tag::Delete] => {
                let [id] = attrs(element, ["ObjectId"])?;
                if let Some(entity) = id.and_then(|id| EntityId::new(id).ok()) {
                    self.deletes.push(scene::Event::new(
                        self.slice(),
                        Operation::Delete { entity },
                    ));
                }
            }
            _ => {
                if let Some((object_at, object)) = self.objects.last_mut() {
                    object.open(&stack[*object_at + 1..], element)?;
                }
            }
        }
        Ok(())
    }

    fn text(&mut self, text: &str) {
        if let Some((_, object)) = self.objects.last_mut() {
            object.text(text);
        }
    }

    /// The last element of `stack` is about to end.
    fn close(&mut self, stack: &[Tag]) -> Result<(), String> {
        let at = stack.len() - 1;
        if let Some((object_at, object)) = self.objects.last_mut() {
            if *object_at != at {
                object.close(&stack[*object_at + 1..]);
                return Ok(());
            }
            let Some((_, object)) = self.objects.pop() else {
                return Ok(());
            };
            self.add(object);
        } else if let [.., Tag::ObjectTree, Tag::Rename] = stack
            && let [Some(from), Some(to)] = std::mem::take(&mut self.rename)
        {
            let operation = Operation::Rename { from, to };
            self.renames
                .push(scene::Event::new(self.slice(), operation));
        }
        Ok(())
    }

    fn slice(&self) -> TimeSlice {
        TimeSlice::at(SceneTime::Timestamp(self.time))
    }

    /// Adds an object to its entity: objects with one id are observations of one entity.
    fn add(&mut self, object: RawObject) {
        let id = object.id.clone();
        let Some(observation) = object.into_observation(SceneTime::Timestamp(self.time)) else {
            return;
        };
        match self.entities.iter_mut().find(|entity| *entity.id() == id) {
            Some(entity) => entity.push_observation(observation),
            None => {
                let mut entity = Entity::new(id);
                entity.push_observation(observation);
                self.entities.push(entity);
            }
        }
    }

    fn into_scene(self) -> Result<Scene, String> {
        let mut events = self.renames;
        events.extend(self.deletes);
        Scene::new(TimeSlice::at(SceneTime::Timestamp(self.time)))
            .with_entities(self.entities)
            .and_then(|scene| scene.with_events(events))
            .map_err(|error| error.to_string())
    }
}
