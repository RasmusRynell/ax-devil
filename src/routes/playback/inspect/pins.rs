//! Targeted pin resolution, separate from the visible page and bounded by the requested pin count.
use super::{PositionRef, page_field};
use api_types::inspect::{InspectionPin, InspectionReadResult, MAX_INSPECTION_PINS};
use std::collections::{BTreeMap, BTreeSet};
#[derive(Default)]
pub(super) struct Pins {
    pub(super) fields: BTreeMap<usize, Vec<usize>>,
    matched: BTreeSet<usize>,
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
    let address = {
        let mut state = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if !state.active {
            return refused();
        }
        if offset == 0 {
            state.pins = Pins::default();
            Vec::new()
        } else if let Some((next, address)) =
            state.pins.next.as_ref().filter(|(next, _)| *next == offset)
        {
            let _ = next;
            address.clone()
        } else {
            return refused();
        }
    };
    let mut matched = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .pins
        .matched
        .clone();
    let page = traversal.page(entity, offset, &address);
    let mut found = Vec::new();
    let mut addresses = BTreeMap::new();
    for (index, mut item) in page.fields.into_iter().enumerate() {
        let matches: Vec<_> = wanted
            .iter()
            .enumerate()
            .filter(|(index, pin)| {
                !matched.contains(index)
                    && pin.group == item.field.group
                    && pin.identity.as_ref().map_or_else(
                        || pin.path == item.field.path,
                        |digest| item.identity.as_ref() == Some(digest),
                    )
            })
            .map(|(index, _)| index)
            .collect();
        if matches.is_empty() {
            continue;
        }
        let ordinal = offset + index;
        addresses.insert(ordinal, item.address.clone());
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
        let field = page_field(item);
        for index in matches {
            matched.insert(index);
            found.push((index as u32, field.clone()));
        }
    }
    let mut state = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if !state.active {
        return refused();
    }
    for (ordinal, address) in addresses {
        if state.pins.fields.len() < MAX_INSPECTION_PINS {
            state.pins.fields.entry(ordinal).or_insert(address);
        }
    }
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
