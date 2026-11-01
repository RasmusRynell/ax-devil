//! The wire form of a viewer's lanes: what the entry shows, how a lane is viewed and where its load stands.

use api_types::playback::{
    ClockWire, FilterOptionInfo, LaneInfo, LaneStatusWire, LaneViewWire, StickyWire, TargetWire,
    WarningInfo,
};
use catalogs::CatalogId;
use foundation::MediaTime;
use overlay_sync::{MatchPolicy, Sticky, Tolerance};
use overlays::{LaneStatus, LaneView, TargetSize};
use scene::{FilterChoice, TimeKind};
use workspace::Entry;

/// How long a kept sample may stay, at most, in milliseconds.
const MAX_STICKY_TIMEOUT_MS: u32 = 100_000;

/// The lanes an entry shows: one per overlay, or one plain lane.
pub(super) fn infos(entry: &Entry) -> Vec<LaneInfo> {
    if entry.overlays.is_empty() {
        return vec![LaneInfo {
            label: entry.label.clone(),
            has_overlay: false,
        }];
    }
    entry
        .overlays
        .iter()
        .map(|overlay| LaneInfo {
            label: overlay.label.clone(),
            has_overlay: true,
        })
        .collect()
}

fn millis(time: MediaTime) -> u32 {
    u32::try_from(time.as_us() / 1000).unwrap_or(u32::MAX)
}

/// The wire form of a lane's view.
pub(super) fn view_wire(view: &LaneView) -> LaneViewWire {
    LaneViewWire {
        catalog: view.catalog.to_string(),
        filter: view.filter.overrides.clone(),
        tolerance_ms: match view.policy.tolerance {
            Tolerance::Exact => None,
            Tolerance::PreviousWithin(time) => Some(millis(time)),
        },
        sticky: StickyWire {
            enabled: view.policy.sticky.enabled,
            timeout_ms: view.policy.sticky.timeout.map(millis),
            opacity: view.policy.sticky.opacity,
        },
        target: TargetWire {
            width: view.target.width,
            height: view.target.height,
        },
        visible: view.visible,
    }
}

/// The view a viewer asked for, or why it cannot be applied, in words for the user.
pub(super) fn view_from_wire(wire: LaneViewWire) -> Result<LaneView, String> {
    let catalog = CatalogId::parse(&wire.catalog)
        .ok_or_else(|| format!("There is no catalog \"{}\"", wire.catalog))?;
    let timeout = match wire.sticky.timeout_ms {
        Some(ms) if ms > MAX_STICKY_TIMEOUT_MS => {
            return Err(format!(
                "A kept overlay can stay at most {} seconds",
                MAX_STICKY_TIMEOUT_MS / 1000
            ));
        }
        Some(ms) => Some(MediaTime::from_us(i64::from(ms) * 1000)),
        None => None,
    };
    if !(0.0..=1.0).contains(&wire.sticky.opacity) {
        return Err("The opacity of a kept overlay is between 0 and 1".to_owned());
    }
    Ok(LaneView {
        catalog,
        filter: FilterChoice {
            overrides: wire.filter,
        },
        policy: MatchPolicy {
            tolerance: match wire.tolerance_ms {
                None => Tolerance::Exact,
                Some(ms) => Tolerance::PreviousWithin(MediaTime::from_us(i64::from(ms) * 1000)),
            },
            sticky: Sticky {
                enabled: wire.sticky.enabled,
                timeout,
                opacity: wire.sticky.opacity,
            },
        },
        // A lane that is not laid out yet reports nothing; lengths in pixels still need a size to measure against,
        // and the viewer sends a new view when the real one is known.
        target: TargetSize {
            width: wire.target.width.max(1),
            height: wire.target.height.max(1),
        },
        visible: wire.visible,
    })
}

/// The wire form of a lane's load status.
pub(super) fn status_wire(status: LaneStatus) -> LaneStatusWire {
    match status {
        LaneStatus::Loading => LaneStatusWire::Loading,
        LaneStatus::Ready(ready) => LaneStatusWire::Ready {
            clock: match ready.clock {
                TimeKind::Frame => ClockWire::Frame,
                TimeKind::Timestamp => ClockWire::Timestamp,
            },
            filter: ready
                .filter
                .options
                .iter()
                .map(|option| FilterOptionInfo {
                    id: option.id.to_string(),
                    label: option.label.to_string(),
                    default_enabled: option.default_enabled,
                })
                .collect(),
            warnings: ready
                .warnings
                .into_iter()
                .map(|warning| WarningInfo {
                    problem: warning.problem,
                    reasons: warning.reasons,
                    consequence: warning.consequence,
                    more_reasons: warning.more_reasons,
                })
                .collect(),
            samples: ready.samples as u64,
        },
        LaneStatus::Failed(error) => LaneStatusWire::Failed {
            reason: error.reason,
            location: error.location,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wire() -> LaneViewWire {
        LaneViewWire {
            catalog: "built-in:tracking".into(),
            filter: [("show_car".to_owned(), false)].into(),
            tolerance_ms: Some(40),
            sticky: StickyWire {
                enabled: true,
                timeout_ms: Some(2050),
                opacity: 0.5,
            },
            target: TargetWire {
                width: 640,
                height: 360,
            },
            visible: false,
        }
    }

    #[test]
    fn a_view_survives_the_trip_and_what_cannot_be_applied_says_why() {
        let view = view_from_wire(wire()).unwrap();
        assert_eq!(view_wire(&view), wire());

        let refused = |change: fn(&mut LaneViewWire)| {
            let mut bad = wire();
            change(&mut bad);
            view_from_wire(bad).unwrap_err()
        };
        assert!(refused(|v| v.catalog = "built-in:nope".into()).contains("built-in:nope"));
        assert!(refused(|v| v.sticky.timeout_ms = Some(100_001)).contains("100 seconds"));
        assert!(refused(|v| v.sticky.opacity = 1.5).contains("opacity"));
        assert!(refused(|v| v.sticky.opacity = f32::NAN).contains("opacity"));

        let mut unmeasured = wire();
        unmeasured.target = TargetWire {
            width: 0,
            height: 0,
        };
        let view = view_from_wire(unmeasured).unwrap();
        assert_eq!((view.target.width, view.target.height), (1, 1));
        let mut unlimited = wire();
        (unlimited.tolerance_ms, unlimited.sticky.timeout_ms) = (None, None);
        assert_eq!(
            view_wire(&view_from_wire(unlimited.clone()).unwrap()),
            unlimited
        );
    }
}
