//! Background work a transition asks for, who owns it, and when it is dropped.
//!
//! A job exists only through [`spawn`]: it gets an id, an owner and a cancel token in the state, and the work itself
//! leaves in the transition's effects. Its completion comes back through the same lock and is dropped when the job is
//! no longer registered or its owner has moved on.

use super::{JobId, SessionState, state::Doc, transition::Effects};
use foundation::{CancelToken, UserError};
use workspace::store::ResolveJob;
use workspace::{Content, ContentId, ItemId};

/// Who a job works for. Its result is dropped when the owner no longer wants it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JobOwner {
    /// Resolving an item for the Doc, for the attempt it waits on.
    Doc {
        /// The item.
        item: ItemId,
        /// The attempt. A newer attempt makes this job stale.
        attempt: u64,
    },
}

/// What a job produces.
#[derive(Debug)]
pub enum Completion {
    /// An item's Content, or why it has none.
    Resolved(Result<Content, UserError>),
}

/// The work of a job.
#[derive(Clone, Debug)]
pub enum Work {
    /// Resolve an item's recipe into its Content.
    Resolve {
        /// The recipe to resolve.
        job: ResolveJob,
        /// Entries currently shown whose replacement media must open before committing.
        shown: Vec<ContentId>,
    },
}

/// A job to run: report its [`Completion`] to the session, and stop early once `cancel` is cancelled.
#[derive(Clone, Debug)]
pub struct Spawn {
    /// Names the job in the session.
    pub job: JobId,
    /// What to do.
    pub work: Work,
    /// Cancelled when the job's result is no longer wanted.
    pub cancel: CancelToken,
    /// Whether the references the job resolves may read this machine's environment (see `SessionState::expands`).
    pub expands: bool,
}

/// A registered job.
#[derive(Clone, Debug)]
pub(super) struct JobEntry {
    owner: JobOwner,
    cancel: CancelToken,
}

/// Registers a job for `owner` and asks for `work` to run.
pub(super) fn spawn(next: &mut SessionState, fx: &mut Effects, owner: JobOwner, work: Work) {
    next.next_job += 1;
    let job = JobId(next.next_job);
    let cancel = CancelToken::new();
    next.jobs.insert(
        job,
        JobEntry {
            owner,
            cancel: cancel.clone(),
        },
    );
    fx.spawn.push(Spawn {
        job,
        work,
        cancel,
        expands: next.expands,
    });
}

/// Takes `job` out of the registry and hands back its owner, or `None` when it is not registered: a completion for
/// it is dropped.
pub(super) fn finish(next: &mut SessionState, job: JobId) -> Option<JobOwner> {
    next.jobs.remove(&job).map(|entry| entry.owner)
}

/// Whether the owner still wants the job's result.
fn wanted(state: &SessionState, owner: &JobOwner) -> bool {
    match owner {
        JobOwner::Doc { item, attempt } => {
            let Doc { workspace } = &*state.doc;
            state.ended.is_none()
                && workspace
                    .workspace
                    .items
                    .iter()
                    .any(|known| known.id == *item)
                && workspace.waits_on(item, *attempt)
        }
    }
}

/// Cancels and unregisters every job whose owner no longer wants it: the session ended, or the item was removed or
/// resolves under a newer attempt.
pub(super) fn sweep(next: &mut SessionState, fx: &mut Effects) {
    let unwanted: Vec<JobId> = next
        .jobs
        .iter()
        .filter(|(_, entry)| !wanted(next, &entry.owner))
        .map(|(id, _)| *id)
        .collect();
    for id in unwanted {
        if let Some(entry) = next.jobs.remove(&id) {
            fx.cancel.push(entry.cancel);
        }
    }
}
