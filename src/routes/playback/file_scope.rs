//! Answers to the File scope requests, each one `ServerMessage::File`. `FileQuery` asks for the objects a lane's whole
//! overlay file holds, as the lane shows them: the query runs here, over the file's history and its placement on the
//! video under the lane's matching, so the viewer is sent one page.
//!
//! One query runs per lane and at most one waits behind it: a newer request cancels the running one, which is never
//! answered, and replaces the waiting one, so a burst of requests never runs more than one blocking job per lane. A
//! query that is refused or cannot be answered is answered at once. Answers name the revisions they were made under and
//! no frame, so the gate never holds frames back; only the viewer's own authority reaches the whole file, so a follower
//! is refused when it asks, and by the writer if it became one while the query ran. The question stays held, and is answered again
//! when the viewer becomes the owner ([`FileScopes::authority`]). The history, placement and listing of the last query
//! of each lane are kept within a byte budget so paging and typing a search do not read them again; a lane the viewer
//! releases keeps nothing.
//!
//! The list, where one object is (`Appearances`) and the lane's recorded events (`EventQuery`, `Event`) are answered
//! beside each other, from the same kept history and placement: none cancels or waits for another kind, and a newer
//! request of its kind replaces its older one ([`latest`], [`appearances`], [`events`]).

pub(super) use self::appearances::Ask as AppearancesAsk;
use self::cache::{CACHE_BYTES, Cache, Cached};
pub(super) use self::events::Ask as EventsAsk;
use self::events::Events;
use self::latest::Latest;
use super::{End, Flow, Outgoing, text};
use api_types::{
    Audience,
    playback::{FileAnswer, FileBody, FileScopeUnavailable, Located, ServerMessage},
    project,
};
use foundation::{CancelToken, Cancelled, ErrorCode, UserError};
use overlays::{FileScopeBlocked, FileScopeInput, ViewerOverlays};
use scene_store::{EventPlacement, HistoryUnavailable, Placement, Query, Search, Sort};
use std::sync::Arc;

/// The most rows a request may ask for.
const MAX_LIMIT: u32 = 200;
/// The most spans of presence a request may ask for per row.
const MAX_BINS: u16 = 256;

/// What a `FileQuery` asked for.
pub(super) struct Ask {
    pub(super) lane: u16,
    pub(super) request: u32,
    pub(super) query: Search,
    pub(super) classes: Vec<u32>,
    pub(super) sort: Sort,
    pub(super) offset: u32,
    pub(super) limit: u32,
    pub(super) bins: u16,
    pub(super) locate: Option<String>,
}

/// A message about one request.
pub(super) struct Reply {
    lane: u16,
    message: ServerMessage,
}

impl Reply {
    /// The answer `answer` to request `request` of `lane`.
    fn file(lane: u16, request: u32, answer: FileAnswer) -> Self {
        Self {
            lane,
            message: ServerMessage::File {
                lane,
                request,
                answer,
            },
        }
    }

    /// The answer that request `request` of `lane` has no answer, and why `why`.
    fn unavailable(lane: u16, request: u32, why: FileScopeUnavailable) -> Self {
        Self::file(lane, request, FileAnswer::Unavailable { unavailable: why })
    }

    /// The answer to request `request` of `lane` whose query crashed. Nothing else answers it, and the viewer sends no
    /// new question while one is outstanding.
    fn crashed(lane: u16, request: u32, audience: Audience) -> Self {
        let error = UserError::new(
            ErrorCode::Internal,
            "The file's objects could not be listed",
        );
        Self::unavailable(
            lane,
            request,
            FileScopeUnavailable::Failed(project(&error, audience)),
        )
    }

    /// Tells the owner what a request of `lane` got wrong, in `reason`.
    fn problem(lane: u16, reason: String) -> Self {
        Self {
            lane,
            message: ServerMessage::LaneProblem { lane, reason },
        }
    }

    /// The message to send. A found answer carries the whole file, so the writer sends it only to a viewer that has
    /// the authority over it when it does; otherwise the viewer is told that it follows, which is built from the
    /// request alone.
    pub(super) fn outgoing(self) -> Outgoing {
        let refusal = match &self.message {
            ServerMessage::File {
                lane,
                request,
                answer: FileAnswer::Found { .. },
            } => {
                let why = FileScopeUnavailable::Following;
                Some(text(&Self::unavailable(*lane, *request, why).message))
            }
            _ => None,
        };
        Outgoing {
            whole_file: refusal.is_some(),
            refusal,
            ..Outgoing::from(text(&self.message))
        }
    }
}

/// What a lane's file can be asked, or why not: a follower is refused, as is a lane with no file or one still being
/// read or failed.
fn input(
    overlays: &ViewerOverlays,
    own: bool,
    lane: u16,
    audience: Audience,
) -> Result<FileScopeInput, FileScopeUnavailable> {
    if !own {
        return Err(FileScopeUnavailable::Following);
    }
    overlays.file_scope(lane).map_err(|blocked| match blocked {
        FileScopeBlocked::Plain => FileScopeUnavailable::PlainLane,
        FileScopeBlocked::Reading => FileScopeUnavailable::Reading,
        FileScopeBlocked::Failed(error) => FileScopeUnavailable::Failed(project(&error, audience)),
    })
}

/// A found answer made under the revisions `cached` holds.
fn found(cached: &Cached, body: FileBody) -> FileAnswer {
    FileAnswer::Found {
        data_revision: cached.data_revision,
        policy_revision: cached.policy_revision,
        body,
    }
}

/// Why a query has no rows.
enum Stop {
    Cancelled,
    Unavailable(FileScopeUnavailable),
}

impl From<Cancelled> for Stop {
    fn from(_: Cancelled) -> Self {
        Self::Cancelled
    }
}

/// A viewer's File scope queries.
pub(super) struct FileScopes {
    audience: Audience,
    /// Whether the viewer had the authority over the whole file at the last refresh and at every question since; a
    /// question taken while it followed clears this, so the next refresh as the owner answers it again.
    was_own: bool,
    cache: Arc<Cache>,
    objects: Latest,
    appearances: Latest,
    events: Events,
}

impl FileScopes {
    /// The queries of a viewer that `audience` is.
    pub(super) fn new(audience: Audience) -> Self {
        Self {
            audience,
            was_own: false,
            cache: Arc::new(Cache::new(CACHE_BYTES)),
            objects: Latest::new(audience),
            appearances: Latest::new(audience),
            events: Events::new(audience),
        }
    }

    /// Takes a request for where one object is, replacing the lane's older one. A lane the video does not have ends
    /// the session.
    pub(super) fn ask_appearances(
        &mut self,
        overlays: &ViewerOverlays,
        own: bool,
        ask: AppearancesAsk,
    ) -> Flow<()> {
        check_lane(overlays, ask.lane)?;
        self.was_own &= own;
        let (lane, request) = (ask.lane, ask.request);
        self.appearances.ask(
            overlays,
            &self.cache,
            own,
            (lane, request),
            move |input, cache, cancel, audience| {
                appearances::answer(&ask, input, cache, cancel, audience)
            },
        );
        Ok(())
    }

    /// Takes a request about events, replacing the lane's older one of its kind. A lane the video does not have ends
    /// the session.
    pub(super) fn ask_events(
        &mut self,
        overlays: &ViewerOverlays,
        own: bool,
        ask: EventsAsk,
    ) -> Flow<()> {
        check_lane(overlays, ask.lane())?;
        self.was_own &= own;
        self.events.ask(overlays, &self.cache, own, ask);
        Ok(())
    }

    /// Takes a request, replacing the lane's older one. `own` says whether the viewer chooses its own frames. A lane
    /// the video does not have ends the session.
    pub(super) fn ask(&mut self, overlays: &ViewerOverlays, own: bool, ask: Ask) -> Flow<()> {
        check_lane(overlays, ask.lane)?;
        self.was_own &= own;
        let (lane, request) = (ask.lane, ask.request);
        if ask.limit == 0 || ask.limit > MAX_LIMIT || ask.bins > MAX_BINS {
            self.objects.release(lane);
            // Only a client that is wrong asks this, and the owner is told what it got wrong.
            if self.audience.host_details {
                let reason = format!(
                    "A file query asks for 1 to {MAX_LIMIT} rows and 0 to {MAX_BINS} spans, not {} and {}",
                    ask.limit, ask.bins
                );
                self.objects.say(Reply::problem(lane, reason));
            }
            return Ok(());
        }
        self.objects.ask(
            overlays,
            &self.cache,
            own,
            (lane, request),
            move |input, cache, cancel, audience| answer(&ask, input, cache, cancel, audience),
        );
        Ok(())
    }

    /// Tells the queries whether the viewer has the authority over the whole file now, and `refused` whether an
    /// answer was refused for want of it. A viewer that became the owner, or was refused as one, is answered again
    /// for the questions it asked while it did not have it.
    pub(super) fn authority(&mut self, overlays: &ViewerOverlays, own: bool, refused: bool) {
        if own && (!self.was_own || refused) {
            self.objects.ask_again(overlays, &self.cache);
            self.appearances.ask_again(overlays, &self.cache);
            self.events.ask_again(overlays, &self.cache);
        }
        self.was_own = own;
    }

    /// Releases only the detail job, independently of list subscriptions.
    pub(super) fn release_event(&mut self, overlays: &ViewerOverlays, lane: u16) -> Flow<()> {
        check_lane(overlays, lane)?;
        self.events.release_detail(lane);
        Ok(())
    }

    /// The lane's list and spans are no longer wanted: its running and waiting queries are dropped without an answer,
    /// and what was kept for it is freed. A lane the video does not have ends the session.
    pub(super) fn release(&mut self, overlays: &ViewerOverlays, lane: u16) -> Flow<()> {
        check_lane(overlays, lane)?;
        self.objects.release(lane);
        self.appearances.release(lane);
        self.events.release(lane);
        self.cache.release(lane);
        Ok(())
    }

    /// The next answer; waits while there is none. A running query that a newer request replaced is not answered.
    pub(super) async fn next(&mut self) -> Reply {
        // Only the sets are polled, so a dropped call loses nothing; none of them ends.
        tokio::select! {
            reply = self.objects.next() => reply,
            reply = self.appearances.next() => reply,
            reply = self.events.next() => reply,
        }
    }
}

/// Ends the session when the video has no `lane`.
fn check_lane(overlays: &ViewerOverlays, lane: u16) -> Flow<()> {
    if usize::from(lane) >= overlays.lane_sources().len() {
        return Err(End::Failed(UserError::new(
            ErrorCode::ProtocolViolation,
            format!("The video has no lane {lane}"),
        )));
    }
    Ok(())
}

/// The answer to the request, or why there is none. The lane's history and placement are reused while the lane's revisions
/// and, for the listing, the query are the same.
fn answer(
    ask: &Ask,
    input: &FileScopeInput,
    cache: &Cache,
    cancel: &CancelToken,
    audience: Audience,
) -> Result<FileAnswer, Stop> {
    let lane = ask.lane;
    let cached = cache.prepared(lane, input, || read(input, cancel, audience))?;
    let revisions = (cached.data_revision, cached.policy_revision);
    let query = query(ask);
    let matches = match &cached.listing {
        Some((listed, matches)) if *listed == query => Arc::clone(matches),
        _ => {
            // The old listing goes while the new one is made.
            cache.update(lane, revisions, |kept| kept.listing = None);
            Arc::new(query.run(&cached.history, &cached.placement, cancel)?)
        }
    };
    let rows = matches.rows(
        &cached.history,
        &cached.placement,
        ask.offset as usize,
        ask.limit as usize,
        ask.bins,
    );
    let located = ask.locate.as_ref().map(|id| Located {
        row: matches.row_of(&cached.history, id),
        id: id.clone(),
    });
    let answer = found(
        &cached,
        FileBody::Objects {
            total: matches.total(),
            matched: matches.matched(),
            offset: ask.offset,
            rows,
            located,
        },
    );
    cache.update(lane, revisions, |kept| {
        kept.listing = Some((query, matches))
    });
    Ok(answer)
}

/// The history and placement of `input`, read now.
fn read(input: &FileScopeInput, cancel: &CancelToken, audience: Audience) -> Result<Cached, Stop> {
    let history = input
        .track
        .history()
        .map_err(|why| unavailable(why, audience))?;
    let placement = Placement::new(
        &input.snapshot.keys,
        input.track.coverage(),
        &input.timeline,
        &input.matching,
        cancel,
    )?;
    let events = EventPlacement::new(
        &history,
        &input.snapshot.keys,
        &input.timeline,
        input.matching.basis.unwrap_or_default(),
        cancel,
    )?;
    Ok(Cached {
        data_revision: input.data_revision,
        policy_revision: input.policy_revision,
        history,
        placement: Arc::new(placement),
        listing: None,
        events: Arc::new(events),
        event_listing: None,
    })
}

fn unavailable(why: HistoryUnavailable, audience: Audience) -> Stop {
    Stop::Unavailable(match why {
        HistoryUnavailable::Reading => FileScopeUnavailable::Reading,
        HistoryUnavailable::TooLarge => FileScopeUnavailable::TooLarge,
        HistoryUnavailable::Failed(error) => {
            FileScopeUnavailable::Failed(project(&error, audience))
        }
    })
}

/// The query a request asks for.
fn query(ask: &Ask) -> Query {
    Query {
        enabled: ask.classes.clone(),
        search: ask.query.clone(),
        sort: ask.sort,
    }
}

mod appearances;
mod cache;
mod events;
mod latest;
#[cfg(test)]
mod tests;
