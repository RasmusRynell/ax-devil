//! An add batch: items added together with a viewer requested are published together, in the order they were added.

use crate::{Content, ItemId};
use foundation::UserError;
use std::collections::HashMap;

use crate::store::{Resolution, WorkspaceState};

/// The items one `AddItems { open: true }` added, each with the attempt that resolves it. Their rows are pending at
/// once; results are held here until every item has a result or no longer waits on its attempt (removed, replaced or
/// resolving again), then the held ones that still match are committed in the order added.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Batch {
    order: Vec<(ItemId, u64)>,
    pub(super) held: HashMap<ItemId, Result<Content, UserError>>,
}

impl Batch {
    /// The batch for the jobs of one add, or none when it added nothing.
    pub(crate) fn of(jobs: &[crate::store::ResolveJob]) -> Option<Self> {
        (!jobs.is_empty()).then(|| Self {
            order: jobs.iter().map(|job| (job.item, job.attempt)).collect(),
            held: HashMap::new(),
        })
    }
}

impl WorkspaceState {
    /// Whether the result of `item` under `attempt` is for a batch to hold.
    pub(crate) fn batched(&self, item: &ItemId, attempt: u64) -> bool {
        self.batches
            .iter()
            .any(|batch| batch.order.contains(&(*item, attempt)))
    }

    /// Holds a result for the batch the item belongs to.
    pub(crate) fn hold(&mut self, item: ItemId, attempt: u64, result: Result<Content, UserError>) {
        if let Some(batch) = self
            .batches
            .iter_mut()
            .find(|batch| batch.order.contains(&(item, attempt)))
        {
            batch.held.insert(item, result);
        }
    }

    /// Drops held results whose item no longer waits on its attempt, then commits every batch that has nothing left
    /// to wait for. Only the first item of a batch that resolved gets a viewer.
    pub(crate) fn publish_ready(&mut self) {
        let mut batches = std::mem::take(&mut self.batches);
        for batch in &mut batches {
            let order = &batch.order;
            batch.held.retain(|item, _| {
                order
                    .iter()
                    .any(|(id, attempt)| id == item && self.waits_on(id, *attempt))
            });
        }
        self.batches = batches;
        while let Some(at) = self.batches.iter().position(|batch| {
            batch.order.iter().all(|(item, attempt)| {
                batch.held.contains_key(item) || !self.waits_on(item, *attempt)
            })
        }) {
            let mut batch = self.batches.remove(at);
            let mut opened = false;
            for (item, attempt) in batch.order {
                let Some(result) = batch.held.remove(&item) else {
                    continue;
                };
                if !self.waits_on(&item, attempt) {
                    continue;
                }
                self.commit(item, attempt, result);
                if !opened
                    && matches!(
                        self.resolutions.get(&item),
                        Some(Resolution::Resolved { .. })
                    )
                {
                    self.open.insert(item);
                    opened = true;
                }
            }
        }
    }
}
