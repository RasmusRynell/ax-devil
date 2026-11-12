//! Targeted pin resolution, separate from the visible page and bounded by the requested pin count.
use super::{PositionRef, page_field};
use api_types::inspect::{InspectionPin, InspectionReadResult, MAX_INSPECTION_PINS};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
#[derive(Default)]
pub(super) struct Pins {
    set: Option<[u8; 32]>,
    pub(super) fields: BTreeMap<usize, Vec<usize>>,
    matched: BTreeMap<usize, usize>,
    next: Option<(usize, Vec<usize>)>,
}
pub(super) fn resolve(
    position: &PositionRef,
    entity: &scene::Entity,
    traversal: &scene::inspection::Traversal,
    wanted: &[InspectionPin],
    offset: usize,
) -> InspectionReadResult {
    let refused = || InspectionReadResult::Refused {
        reason: "Pinned inspection expired; select the object again".into(),
    };
    if wanted.len() > MAX_INSPECTION_PINS
        || wanted
            .iter()
            .map(|pin| {
                pin.path.iter().map(String::len).sum::<usize>()
                    + pin.identity.as_ref().map_or(0, String::len)
            })
            .sum::<usize>()
            > scene::inspection::MAX_INSPECTION_BYTES
    {
        return InspectionReadResult::Refused {
            reason: "Pinned fields exceed the inspection budget".into(),
        };
    }
    let set = signature(wanted);
    let (address, mut matched, mut addresses) = {
        let mut state = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if !state.active {
            return refused();
        }
        if state.pins.set != Some(set) {
            state.pins = Pins {
                set: Some(set),
                ..Pins::default()
            };
            if offset != 0 {
                return refused();
            }
        }
        let address = if offset == 0 {
            Vec::new()
        } else if let Some((next, address)) =
            state.pins.next.as_ref().filter(|(next, _)| *next == offset)
        {
            let _ = next;
            address.clone()
        } else {
            return refused();
        };
        (
            address,
            state.pins.matched.clone(),
            state.pins.fields.clone(),
        )
    };
    let mut found = Vec::new();
    if offset == 0 {
        // A retained match says nothing about client receipt. Replay it on every restart, without caching text.
        matched.retain(|index, ordinal| {
            let item = addresses
                .get(ordinal)
                .and_then(|address| traversal.preview(entity, *ordinal, address))
                .filter(|item| matches(&wanted[*index], item));
            if let Some(item) = item {
                found.push((*index as u32, pin_field(item, *ordinal)));
                true
            } else {
                false
            }
        });
        addresses.retain(|ordinal, _| matched.values().any(|value| value == ordinal));
    }
    let page = if matched.len() == wanted.len() {
        scene::inspection::FieldPage {
            fields: Vec::new(),
            next: None,
            next_address: None,
        }
    } else {
        traversal.page(entity, offset, &address)
    };
    for (index, item) in page.fields.into_iter().enumerate() {
        let matches: Vec<_> = wanted
            .iter()
            .enumerate()
            .filter(|(index, pin)| !matched.contains_key(index) && matches(pin, &item))
            .map(|(index, _)| index)
            .collect();
        if matches.is_empty() {
            continue;
        }
        let ordinal = offset + index;
        addresses.insert(ordinal, item.address.clone());
        let field = pin_field(item, ordinal);
        for index in matches {
            matched.insert(index, ordinal);
            found.push((index as u32, field.clone()));
        }
    }
    let mut state = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if !state.active {
        return refused();
    }
    state.pins.fields = addresses;
    let next = (matched.len() < wanted.len())
        .then_some(page.next)
        .flatten();
    state.pins.matched = matched;
    state.pins.next = next.zip(page.next_address);
    InspectionReadResult::Pins {
        fields: found,
        next: next.map(|n| n as u32),
    }
}

fn matches(pin: &InspectionPin, item: &scene::inspection::PageField) -> bool {
    pin.group == item.field.group
        && pin.identity.as_ref().map_or_else(
            || pin.path == item.field.path,
            |digest| item.identity.as_ref() == Some(digest),
        )
}

fn pin_field(
    mut item: scene::inspection::PageField,
    ordinal: usize,
) -> api_types::inspect::FieldWire {
    // Pin labels use a bounded preview even when an ordinary page can accommodate a longer path.
    let total = item.field.path.iter().map(String::len).sum::<usize>()
        + item.field.path.len().saturating_sub(1);
    if total > 64 << 10 {
        let mut left = 64 << 10;
        for part in &mut item.field.path {
            let mut end = left.min(part.len());
            while !part.is_char_boundary(end) {
                end -= 1;
            }
            part.truncate(end);
            left -= end;
        }
        item.path_cut.get_or_insert((ordinal, total));
    }
    page_field(item)
}

// Identify the ordered pin set without retaining its potentially large legacy paths.
fn signature(wanted: &[InspectionPin]) -> [u8; 32] {
    let mut hash = Sha256::new();
    for pin in wanted {
        hash.update([pin.group as u8, u8::from(pin.identity.is_some())]);
        hash.update((pin.path.len() as u64).to_le_bytes());
        for part in std::iter::once(pin.identity.as_deref().unwrap_or(""))
            .chain(pin.path.iter().map(String::as_str))
        {
            hash.update((part.len() as u64).to_le_bytes());
            hash.update(part.as_bytes());
        }
    }
    hash.finalize().into()
}
