//! Field previews projected onto the playback wire.
use api_types::inspect::FieldWire;
use scene::inspection::InspectionField;
pub(super) fn page_field(item: scene::inspection::PageField) -> FieldWire {
    let mut wire = field(item.field);
    wire.identity = item.identity;
    wire.cut = item
        .cut
        .map(|(ordinal, bytes)| [ordinal as u64, bytes as u64]);
    wire.path_cut = item
        .path_cut
        .map(|(ordinal, bytes)| [ordinal as u64, bytes as u64]);
    wire
}

pub(super) fn field(field: InspectionField) -> FieldWire {
    FieldWire {
        identity: None,
        cut: None,
        path_cut: None,
        group: field.group,
        origin: field.origin,
        path: field.path,
        value: field.value,
        rgb: field.color.map(|color| [color.r, color.g, color.b]),
        text: field.text,
        fraction: field.fraction,
    }
}
