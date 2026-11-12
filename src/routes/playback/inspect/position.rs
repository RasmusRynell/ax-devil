//! Bounded state retained by one socket-local inspection capability.
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

/// Only addresses of the current page and its next boundary are retained, never a flattened list.
pub(super) struct Position {
    pub(super) validity: foundation::CancelToken,
    /// One shared reference to the immutable held input; no flattened fields or copied texts.
    pub(super) scene: Option<Arc<scene::Scene>>,
    pub(super) pins: super::pins::Pins,
    pub(super) fields: BTreeMap<usize, Vec<usize>>,
    pub(super) next: Option<(usize, Vec<usize>)>,
    pub(super) raw: Option<scene::inspection::RawCopy>,
    pub(super) traversal: Option<Arc<scene::inspection::Traversal>>,
}

impl Default for Position {
    fn default() -> Self {
        Self {
            validity: foundation::CancelToken::new(),
            scene: None,
            pins: super::pins::Pins::default(),
            fields: BTreeMap::new(),
            next: None,
            raw: None,
            traversal: None,
        }
    }
}

pub(super) type PositionRef = Arc<Mutex<Position>>;

pub(super) fn validity(position: &PositionRef) -> foundation::CancelToken {
    position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .validity
        .clone()
}

pub(super) fn remember(position: &PositionRef, first: usize, page: &scene::inspection::FieldPage) {
    let fields = page
        .fields
        .iter()
        .enumerate()
        .map(|(i, field)| (first + i, field.address.clone()))
        .collect();
    let next = page.next.zip(page.next_address.clone());
    let old = {
        let mut state = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.validity.is_cancelled() {
            return;
        }
        (
            std::mem::replace(&mut state.fields, fields),
            std::mem::replace(&mut state.next, next),
        )
    };
    drop(old);
}

pub(super) fn invalidate(position: &PositionRef) {
    let retired = {
        let mut state = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.validity.cancel();
        let validity = state.validity.clone();
        std::mem::replace(
            &mut *state,
            Position {
                validity,
                ..Position::default()
            },
        )
    };
    // Last-reference Scene destruction can be proportional to the input, so it belongs with blocking work too.
    if let Ok(runtime) = tokio::runtime::Handle::try_current() {
        runtime.spawn_blocking(move || drop(retired));
    } else {
        // Synchronous Scene-owner callers have no async worker to protect.
        drop(retired);
    }
}

/// Takes only the cursor under the lock; sizing/emission must never block the socket's release transition.
pub(super) fn raw(
    position: &PositionRef,
    entity: &scene::Entity,
    offset: usize,
) -> api_types::inspect::InspectionReadResult {
    raw_with(position, offset, |cursor| {
        scene::inspection::raw_chunk(entity, cursor, offset, &validity(position))
    })
}

fn raw_with(
    position: &PositionRef,
    offset: usize,
    work: impl FnOnce(&mut scene::inspection::RawCopy) -> Result<scene::inspection::FieldChunk, String>,
) -> api_types::inspect::InspectionReadResult {
    use api_types::inspect::InspectionReadResult;
    let mut cursor = {
        let mut state = position
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if offset == 0 && !state.validity.is_cancelled() {
            state.raw = Some(scene::inspection::RawCopy::default());
        }
        state.raw.take()
    };
    let Some(cursor) = cursor.as_mut() else {
        return InspectionReadResult::Refused {
            reason: "Inspection expired; select the object again".into(),
        };
    };
    let result = work(cursor);
    let mut state = position
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if state.validity.is_cancelled() {
        return InspectionReadResult::Refused {
            reason: "Inspection expired; select the object again".into(),
        };
    }
    state.raw = Some(std::mem::take(cursor));
    match result {
        Ok(chunk) => InspectionReadResult::Chunk {
            text: chunk.text,
            total: chunk.total as u32,
            next: chunk.next.map(|n| n as u32),
        },
        Err(reason) => InspectionReadResult::Refused { reason },
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{ReleaseOnDrop, bounded};
    use super::*;
    #[tokio::test(flavor = "current_thread")]
    async fn release_does_not_wait_for_a_slow_raw_job_or_retain_its_result() {
        let position = PositionRef::default();
        let held = position.clone();
        let (started, ready) = tokio::sync::oneshot::channel();
        let (release, wait) = std::sync::mpsc::channel();
        let release = ReleaseOnDrop(release);
        let job = tokio::task::spawn_blocking(move || {
            raw_with(&held, 0, |_| {
                started.send(()).unwrap();
                wait.recv_timeout(std::time::Duration::from_secs(20))
                    .expect("held Raw job released in time");
                Ok(scene::inspection::FieldChunk {
                    text: "done".into(),
                    total: 4,
                    next: None,
                })
            })
        });
        bounded("held Raw job started", ready).await.unwrap();
        // An independent thread releases even if a regression blocks this single runtime worker; the assertion
        // below then fails instead of leaving the test process hung.
        let (did_release, released) = std::sync::mpsc::channel();
        let watchdog = std::thread::spawn(move || {
            let early = released
                .recv_timeout(std::time::Duration::from_secs(2))
                .is_ok();
            drop(release);
            early
        });
        invalidate(&position);
        let _ = did_release.send(());
        assert!(
            watchdog.join().unwrap(),
            "socket release waited for Raw work"
        );
        assert!(matches!(
            bounded("released Raw job finished", job).await.unwrap(),
            api_types::inspect::InspectionReadResult::Refused { .. }
        ));
        assert!(position.lock().unwrap().raw.is_none());
    }
}
