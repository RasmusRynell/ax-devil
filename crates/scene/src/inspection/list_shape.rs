//! Bounded, resumable colour-list recognition. Expensive decisions of the current page are reused by chunks.
use crate::{ColorEntry, Value};
use std::collections::VecDeque;

/// Eight expensive lists cover a page: each inspected prefix of 1024 colours produces at least 1024 leaves.
#[derive(Default)]
pub(super) struct ListShapes {
    known: VecDeque<(Vec<usize>, bool)>,
    pending: Option<(Vec<usize>, usize)>,
}
impl ListShapes {
    pub(super) fn check(
        &mut self,
        address: &[usize],
        items: &[Value],
        work: &mut usize,
        cancel: &foundation::CancelToken,
    ) -> Option<bool> {
        if let Some((_, answer)) = self.known.iter().find(|(key, _)| key == address) {
            return Some(*answer);
        }
        if items.is_empty() {
            return Some(false);
        }
        let mut checked = self
            .pending
            .as_ref()
            .filter(|(key, _)| key == address)
            .map_or(0, |(_, n)| *n);
        let mut answer = true;
        while checked < items.len() {
            if cancel.is_cancelled() {
                return None;
            }
            if *work == 0 {
                self.pending = Some((address.to_vec(), checked));
                return None;
            }
            *work -= 1;
            let color = ColorEntry::read_exact(&items[checked]).is_some();
            checked += 1;
            if !color {
                answer = false;
                break;
            }
        }
        if self.pending.as_ref().is_some_and(|(key, _)| key == address) {
            self.pending = None;
        }
        if checked >= 1024 {
            self.known.push_back((address.to_vec(), answer));
            if self.known.len() > 8 {
                self.known.pop_front();
            }
        }
        Some(answer)
    }
}
