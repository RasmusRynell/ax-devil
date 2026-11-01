use foundation::MediaTime;

use crate::SceneError;

/// When something applies: a frame number or a Media Time timestamp.
///
/// The order is only meaningful between times of one [`TimeKind`]; a Scene never mixes kinds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SceneTime {
    /// A frame number of the video the overlay belongs to, from 0.
    Frame(u64),
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

/// What a file says about the frames it has no Scene for. Only meaningful for frame-keyed files.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Coverage {
    /// A frame without a Scene was not described: nothing is known about it.
    Sparse,
    /// A frame without a Scene was described as empty.
    Dense,
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

    /// Whether the slice is a single moment.
    pub fn is_instant(&self) -> bool {
        self.start == self.end
    }

    /// Which clock the slice is on.
    pub fn kind(&self) -> TimeKind {
        self.start.kind()
    }
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
        assert_eq!(
            TimeSlice::new(frame(0), stamp(1)),
            Err(SceneError::TimeKindMismatch)
        );
    }
}
