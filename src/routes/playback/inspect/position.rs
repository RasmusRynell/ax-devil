//! Bounded state retained by one socket-local inspection capability.
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

/// Only addresses of the current page and its next boundary are retained, never a flattened list.
pub(super) struct Position {
    pub(super) active: bool,
    /// One shared reference to the immutable held input; no flattened fields or copied texts.
    pub(super) scene: Option<Arc<scene::Scene>>,
    pub(super) fields: BTreeMap<usize, Vec<usize>>,
    pub(super) next: Option<(usize, Vec<usize>)>,
    pub(super) raw: Option<scene::inspection::RawCopy>,
    pub(super) traversal: Option<Arc<scene::inspection::Traversal>>,
}

impl Default for Position {
    fn default() -> Self {
        Self {
            active: true,
            scene: None,
            fields: BTreeMap::new(),
            next: None,
            raw: None,
            traversal: None,
        }
    }
}

pub(super) type PositionRef = Arc<Mutex<Position>>;

pub(super) fn remember(position: &PositionRef, first: usize, page: &scene::inspection::FieldPage) {
    let mut position = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if !position.active {
        return;
    }
    position.fields = page
        .fields
        .iter()
        .enumerate()
        .map(|(i, field)| (first + i, field.address.clone()))
        .collect();
    position.next = page.next.zip(page.next_address.clone());
}

pub(super) fn invalidate(position: &PositionRef) {
    let mut position = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    position.active = false;
    position.traversal = None;
    position.scene = None;
    position.fields.clear();
    position.raw = None;
    position.next = None;
}
