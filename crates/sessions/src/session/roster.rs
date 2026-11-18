//! Who is in the session, for the roster every seat sees: names, colors and join order.
//!
//! This is presence data, kept next to the cursors and outside the revisioned [`SessionState`]: it does not decide
//! what any seat may do, and a name or color is not worth a revision. Join order is the seat id, which only grows, so
//! nothing is stored for it. A seat keeps the [`Identity`] it was given for its whole life, resumes included.

use super::{SeatId, SessionState};
use crate::{SeatMode, SeatRole};
use serde::Serialize;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use ts_rs::TS;

/// What names a seat to the others: `Guest N` and a color index into the UI's palette.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Identity {
    number: u32,
    color: u8,
}

impl Identity {
    /// The lowest number and color that no seat in `taken` has.
    pub(super) fn next<'a>(taken: impl Iterator<Item = &'a Identity> + Clone) -> Self {
        let number = (1u32..)
            .find(|number| taken.clone().all(|other| other.number != *number))
            .unwrap_or_default();
        let color = (0..=u8::MAX)
            .find(|color| taken.clone().all(|other| other.color != *color))
            .unwrap_or_default();
        Self { number, color }
    }
}

/// One seat of a Session, as every seat sees it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "SeatInfo")]
pub struct RosterEntry {
    /// Names the seat in `Roster` and `Cursor` messages.
    #[serde(rename = "id")]
    #[ts(type = "number")]
    pub seat: SeatId,
    /// `Guest N`.
    pub name: String,
    /// An index into the UI's palette of participant colors.
    pub color: u8,
    /// Owner or Guest.
    #[serde(rename = "role")]
    pub kind: SeatRole,
    /// Following or exploring.
    pub mode: SeatMode,
    /// False while the seat is within its grace period.
    pub present: bool,
    /// Whether the seat broadcasts its cursor.
    pub shares_cursor: bool,
}

/// What the service keeps per seat for the roster.
pub(super) struct Presence {
    pub identity: Identity,
    /// Whether the seat broadcasts its cursor; the seat's handle changes it.
    pub share: Arc<AtomicBool>,
}

/// The roster of `state`, oldest seat first. A seat the service has not finished seating is left out.
pub(super) fn build<'a>(
    state: &SessionState,
    presence: impl Fn(SeatId) -> Option<&'a Presence>,
) -> Vec<RosterEntry> {
    state
        .seats
        .iter()
        .filter_map(|(seat, state)| {
            let presence = presence(*seat)?;
            Some(RosterEntry {
                seat: *seat,
                name: format!("Guest {}", presence.identity.number),
                color: presence.identity.color,
                kind: match state.kind {
                    super::SeatKind::Owner => SeatRole::Owner,
                    super::SeatKind::Guest { .. } => SeatRole::Guest,
                },
                mode: state.mode(),
                present: state.present,
                shares_cursor: presence.share.load(Ordering::Relaxed),
            })
        })
        .collect()
}
