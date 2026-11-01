//! Background work a transition asks for, who owns it, and when it is dropped.
//!
//! A job exists only through [`spawn`]: it gets an id, an owner and a cancel token in the state, and the work itself
//! leaves in the transition's effects. Its completion comes back through the same lock and is dropped when the job is
//! no longer registered or its owner has moved on.

use super::{
    ContentRef, JobId, Lease, SeatId, SessionState, Use, authorize::authorize, state::Doc,
    transition::Effects,
};
use foundation::{CancelToken, UserError};
use workspace::{Content, ItemId};
use workspace_store::{Resolution, ResolveJob};

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
    /// A computation private to one seat. Reserved: no computation exists yet.
    Seat {
        /// The seat.
        seat: SeatId,
        /// The seat's lease when it asked.
        lease: Lease,
        /// What it computes from.
        of: ContentRef,
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
    Resolve(ResolveJob),
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
    fx.spawn.push(Spawn { job, work, cancel });
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
                && workspace.resolutions.get(item)
                    == Some(&Resolution::Pending { attempt: *attempt })
        }
        JobOwner::Seat { seat, lease, of } => {
            authorize(state, *seat, *lease, Use::Compute(of)).is_ok()
        }
    }
}

/// Cancels and unregisters every job whose owner no longer wants it: the session ended, the item was removed or
/// resolves under a newer attempt, or the seat left, was replaced or lost its permission.
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{OwnerId, SessionId};
    use workspace_store::ContentGen;

    #[test]
    fn a_seat_job_is_swept_because_no_seat_may_compute_yet() {
        let mut state = SessionState::new(SessionId::generate(), OwnerId::generate(), true);
        let mut fx = Effects::default();
        let of = ContentRef {
            content: workspace::ContentId::of_item(&ItemId::generate()),
            generation: ContentGen(1),
        };
        let owner = JobOwner::Seat {
            seat: super::super::OWNER_SEAT,
            lease: Lease(0),
            of,
        };
        let work = Work::Resolve(ResolveJob {
            item: ItemId::generate(),
            attempt: 1,
            recipe: workspace::Recipe::Video(workspace::VideoRecipe { video: "x".into() }),
        });
        spawn(&mut state, &mut fx, owner, work);

        sweep(&mut state, &mut fx);

        assert!(state.jobs.is_empty());
        assert_eq!(fx.cancel.len(), 1);
    }
}
