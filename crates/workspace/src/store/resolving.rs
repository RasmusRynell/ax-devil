//! Resolving an item: the jobs that read its Content, and committing their results.

use crate::{Content, Environment, ItemId, Recipe, WorkspaceItem};
use foundation::{CancelToken, Interrupt, UserError};
use handlers::Handlers;

use crate::store::{ContentGen, Resolution, WorkspaceState};

/// An open viewer's item read again while its old Content stays: it is the Content the viewer shows until a reload
/// succeeds, and a failed one leaves it in place with the reason.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Reload {
    /// Reading again has not finished; only the result of this attempt is accepted.
    Running {
        /// The attempt that is running.
        attempt: u64,
        /// The changes to the files counted when it started: it read at least those.
        seen: u32,
    },
    /// The last reload failed; the viewer keeps the old Content and says why until the next attempt.
    Failed {
        /// The attempt that failed, which tells one failure from the next.
        attempt: u64,
        /// Why.
        error: UserError,
    },
}

/// Work a transition asks its caller to run: resolve `recipe`, then hand the result to [`complete`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolveJob {
    /// The item being resolved.
    pub item: ItemId,
    /// The attempt the item waits on; pass it back to [`complete`].
    pub attempt: u64,
    /// The item's own label at the time, empty when it has none.
    pub label: String,
    /// What to resolve.
    pub recipe: Recipe,
}

impl WorkspaceState {
    /// Marks the item pending under a new attempt and returns the job for it.
    pub(crate) fn start(&mut self, item: &WorkspaceItem) -> ResolveJob {
        self.pin_hidden(&item.id);
        self.changed.remove(&item.id);
        self.reloads.remove(&item.id);
        let job = self.job(item);
        self.resolutions.insert(
            item.id,
            Resolution::Pending {
                attempt: job.attempt,
            },
        );
        job
    }

    /// Starts reading the item again under a new attempt while its Content stays what the viewer shows. The count of
    /// changes to its files only grows until a reload succeeds, so what the user dismissed stays dismissed; a change
    /// that arrives meanwhile counts beyond `seen`, since the attempt may have read the old files.
    pub(crate) fn start_reload(&mut self, item: &WorkspaceItem) -> ResolveJob {
        self.pin_hidden(&item.id);
        let job = self.job(item);
        self.reloads.insert(
            item.id,
            Reload::Running {
                attempt: job.attempt,
                seen: self
                    .changed
                    .get(&item.id)
                    .map_or(0, |changes| changes.revision),
            },
        );
        job
    }

    fn job(&mut self, item: &WorkspaceItem) -> ResolveJob {
        self.next_attempt += 1;
        ResolveJob {
            item: item.id,
            attempt: self.next_attempt,
            label: item.label.clone(),
            recipe: item.recipe.clone(),
        }
    }

    /// Whether `item` is resolving under `attempt`: only that attempt's result is accepted.
    pub fn waits_on(&self, item: &ItemId, attempt: u64) -> bool {
        self.resolutions.get(item) == Some(&Resolution::Pending { attempt })
            || matches!(self.reloads.get(item), Some(Reload::Running { attempt: a, .. }) if *a == attempt)
    }

    /// Whether the item is resolving, as a first resolution or a reload.
    pub(crate) fn resolving(&self, item: &ItemId) -> bool {
        matches!(self.resolutions.get(item), Some(Resolution::Pending { .. }))
            || matches!(self.reloads.get(item), Some(Reload::Running { .. }))
    }
}

/// Commits the result of resolving `item` under `attempt`. A result whose item no longer waits on that attempt is
/// dropped: the returned state equals `state`. A reload that fails leaves the old Content, with the reason in
/// [`WorkspaceState::reloads`], unless the files changed again meanwhile: the viewer's offer to reload them stands.
/// An item added with a viewer requested holds its result until its whole batch has one.
pub fn complete(
    state: &WorkspaceState,
    item: ItemId,
    attempt: u64,
    result: Result<Content, UserError>,
) -> WorkspaceState {
    let mut next = state.clone();
    if !next.waits_on(&item, attempt) {
        return next;
    }
    if next.batched(&item, attempt) {
        next.hold(item, attempt, result);
        next.publish_ready();
    } else {
        next.commit(item, attempt, result);
    }
    next
}

impl WorkspaceState {
    /// Records the result of an item that waits on `attempt`.
    pub(crate) fn commit(
        &mut self,
        item: ItemId,
        attempt: u64,
        result: Result<Content, UserError>,
    ) {
        let reloading = match self.reloads.remove(&item) {
            Some(Reload::Running { seen, .. }) => Some(seen),
            _ => None,
        };
        let counted = self
            .changed
            .get(&item)
            .map_or(0, |changes| changes.revision);
        let resolution = match result {
            Ok(content) => {
                self.settle_hidden(&item, &content);
                self.next_generation += 1;
                // The new Content has read the changes counted before the reload, not those that came during it.
                if let Some(changes) = self.changed.get_mut(&item) {
                    changes.covered(reloading.unwrap_or(counted));
                    let ids: std::collections::HashSet<_> =
                        content.entries().iter().map(|entry| &entry.id).collect();
                    changes.entries.retain(|entry, _| ids.contains(entry));
                    if changes.revision == 0 {
                        self.changed.remove(&item);
                    }
                }
                Resolution::Resolved {
                    content,
                    generation: ContentGen(self.next_generation),
                }
            }
            Err(error) if reloading.is_some() => {
                if Some(counted) == reloading {
                    self.reloads.insert(item, Reload::Failed { attempt, error });
                }
                return;
            }
            Err(error) => Resolution::Failed(error),
        };
        self.resolutions.insert(item, resolution);
    }
}

/// Runs a job with the installed `handlers`, expanding `$NAME` references from `env`. Reads the file system, so callers
/// keep it off an async executor's workers. A cancelled job gives [`Interrupt::Cancelled`], which the caller reports
/// to no one.
pub fn resolve(
    job: &ResolveJob,
    handlers: &Handlers,
    env: &Environment,
    cancel: &CancelToken,
) -> Result<Content, Interrupt<UserError>> {
    job.recipe
        .resolve(&job.item, &job.label, handlers, env, cancel)
}
