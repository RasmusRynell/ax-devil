use foundation::MediaTime;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::SceneError;

/// When something applies: a frame number or a Media Time timestamp.
///
/// Frame keys sort before timestamp keys; elapsed time is meaningful only within one [`TimeKind`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SceneTime {
    /// A frame number of the video the overlay belongs to. Negative numbers are valid here; a decoder may give one
    /// for a moment before the video starts.
    Frame(i64),
    /// A time on the Media Time clock.
    Timestamp(MediaTime),
}

/// Which clock a [`SceneTime`] is on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum TimeKind {
    /// Frame numbers.
    Frame,
    /// Timestamps.
    Timestamp,
}

impl SceneTime {
    /// Which clock the time is on.
    pub fn kind(self) -> TimeKind {
        match self {
            Self::Frame(_) => TimeKind::Frame,
            Self::Timestamp(_) => TimeKind::Timestamp,
        }
    }
}

/// When an observation was made: a frame number, a timestamp, or both.
///
/// The two clocks are independent facts and are never converted into each other. A Scene is on one of them
/// ([`TimeSlice`]); observations need not carry that same clock.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ObservationTime {
    frame: Option<i64>,
    timestamp: Option<MediaTime>,
}

impl ObservationTime {
    /// An observation time with the given clocks; at least one must be present.
    pub fn new(frame: Option<i64>, timestamp: Option<MediaTime>) -> Result<Self, SceneError> {
        if frame.is_none() && timestamp.is_none() {
            return Err(SceneError::NoObservationTime);
        }
        Ok(Self { frame, timestamp })
    }

    /// The frame number, if the observation has one.
    pub fn frame(&self) -> Option<i64> {
        self.frame
    }

    /// The timestamp, if the observation has one.
    pub fn timestamp(&self) -> Option<MediaTime> {
        self.timestamp
    }

    /// The time on the `kind` clock, if the observation has one.
    pub fn on(&self, kind: TimeKind) -> Option<SceneTime> {
        match kind {
            TimeKind::Frame => self.frame.map(SceneTime::Frame),
            TimeKind::Timestamp => self.timestamp.map(SceneTime::Timestamp),
        }
    }
}

impl From<SceneTime> for ObservationTime {
    fn from(time: SceneTime) -> Self {
        match time {
            SceneTime::Frame(frame) => Self {
                frame: Some(frame),
                timestamp: None,
            },
            SceneTime::Timestamp(time) => Self {
                frame: None,
                timestamp: Some(time),
            },
        }
    }
}

/// What a file says about the frames it has no Scene for. Only meaningful for frame-keyed files.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Coverage {
    /// A frame without a Scene was not described: nothing is known about it.
    Sparse,
    /// A frame without a Scene was described as empty.
    Dense,
}

/// What a lane matches records to video frames by. The host chooses it per lane; a decoder only declares which
/// bases it supports and which it recommends.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize, TS)]
pub enum MatchBasis {
    /// By frame number. A time-keyed file's frame number is the record's position in the file: record k belongs to
    /// frame k.
    #[default]
    Frame,
    /// By the sample's timestamp on the video's Media Time clock.
    Time,
}

/// A span of time on one clock, ends included.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TimeSlice {
    start: SceneTime,
    end: SceneTime,
}

impl TimeSlice {
    /// The slice that is the single moment `time`.
    pub fn at(time: SceneTime) -> Self {
        Self {
            start: time,
            end: time,
        }
    }

    /// The slice from `start` to `end`; both must be on one clock and `start` must not be after `end`.
    pub fn new(start: SceneTime, end: SceneTime) -> Result<Self, SceneError> {
        if start.kind() != end.kind() {
            return Err(SceneError::TimeKindMismatch);
        }
        if start > end {
            return Err(SceneError::ReversedTimeSlice);
        }
        Ok(Self { start, end })
    }

    /// When the slice starts.
    pub fn start(&self) -> SceneTime {
        self.start
    }

    /// When the slice ends.
    pub fn end(&self) -> SceneTime {
        self.end
    }

    /// The start as a timestamp; `None` for a slice of frame numbers.
    pub fn start_timestamp(&self) -> Option<MediaTime> {
        match self.start {
            SceneTime::Timestamp(time) => Some(time),
            SceneTime::Frame(_) => None,
        }
    }

    /// How long the slice lasts: seconds for timestamps, the number of frames from start to end for frame numbers.
    pub fn duration(&self) -> f64 {
        match (self.start, self.end) {
            (SceneTime::Timestamp(start), SceneTime::Timestamp(end)) => {
                (i128::from(end.0) - i128::from(start.0)) as f64 / 1e6
            }
            (SceneTime::Frame(start), SceneTime::Frame(end)) => {
                (i128::from(end) - i128::from(start)) as f64
            }
            // `new` refuses a slice on two clocks.
            _ => 0.0,
        }
    }

    /// Whether the slice is a single moment.
    pub fn is_instant(&self) -> bool {
        self.start == self.end
    }

    /// Which clock the slice is on.
    pub fn kind(&self) -> TimeKind {
        self.start.kind()
    }
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<MatchBasis>();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_slice_stays_on_one_clock_and_runs_forward() {
        let frame = SceneTime::Frame;
        let stamp = |us| SceneTime::Timestamp(MediaTime(us));

        assert_eq!(
            TimeSlice::new(frame(2), frame(2)),
            Ok(TimeSlice::at(frame(2)))
        );
        assert!(TimeSlice::at(frame(2)).is_instant());
        assert_eq!(
            TimeSlice::new(stamp(1), stamp(5)).map(|s| s.kind()),
            Ok(TimeKind::Timestamp)
        );
        assert_eq!(
            TimeSlice::new(frame(3), frame(2)),
            Err(SceneError::ReversedTimeSlice)
        );
        let span = |start, end| TimeSlice::new(start, end).expect("a slice");
        assert_eq!(span(frame(-2), frame(5)).duration(), 7.0);
        assert_eq!(span(stamp(1_000_000), stamp(3_500_000)).duration(), 2.5);
        assert_eq!(TimeSlice::at(frame(4)).duration(), 0.0);
        assert_eq!(
            span(stamp(7), stamp(9)).start_timestamp(),
            Some(MediaTime(7))
        );
        assert_eq!(span(frame(0), frame(1)).start_timestamp(), None);
        assert_eq!(
            TimeSlice::new(frame(0), stamp(1)),
            Err(SceneError::TimeKindMismatch)
        );
    }
}
