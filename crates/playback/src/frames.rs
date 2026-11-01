use std::ops::RangeInclusive;

/// A set of frame numbers kept as sorted, disjoint inclusive ranges, so a cache holding thousands of consecutive
/// frames is a handful of ranges.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FrameSet {
    ranges: Vec<RangeInclusive<u64>>,
}

impl From<RangeInclusive<u64>> for FrameSet {
    fn from(range: RangeInclusive<u64>) -> Self {
        Self::from_ranges(std::iter::once(range))
    }
}

impl FrameSet {
    /// An empty set.
    pub fn new() -> Self {
        Self::default()
    }

    /// The union of `ranges`.
    pub fn from_ranges(ranges: impl IntoIterator<Item = RangeInclusive<u64>>) -> Self {
        let mut set = Self::new();
        for range in ranges {
            set.insert_range(range);
        }
        set
    }

    /// The members as sorted, disjoint, non-adjacent ranges.
    pub fn ranges(&self) -> &[RangeInclusive<u64>] {
        &self.ranges
    }

    /// Whether `id` is a member.
    pub fn contains(&self, id: u64) -> bool {
        self.range_containing(id).is_some()
    }

    /// The range holding `id`.
    pub fn range_containing(&self, id: u64) -> Option<&RangeInclusive<u64>> {
        let index = self.ranges.partition_point(|range| *range.end() < id);
        self.ranges.get(index).filter(|range| *range.start() <= id)
    }

    /// Removes every member.
    pub fn clear(&mut self) {
        self.ranges.clear();
    }

    /// Adds one frame.
    pub fn insert(&mut self, id: u64) {
        self.insert_range(id..=id);
    }

    /// Adds every frame of `range`, merging with neighbours.
    pub fn insert_range(&mut self, range: RangeInclusive<u64>) {
        let (mut start, mut end) = (*range.start(), *range.end());
        if start > end {
            return;
        }
        let first = self
            .ranges
            .partition_point(|other| other.end().saturating_add(1) < start);
        let mut after = first;
        while after < self.ranges.len() && *self.ranges[after].start() <= end.saturating_add(1) {
            start = start.min(*self.ranges[after].start());
            end = end.max(*self.ranges[after].end());
            after += 1;
        }
        self.ranges
            .splice(first..after, std::iter::once(start..=end));
    }

    /// Removes one frame; returns whether it was a member.
    pub fn remove(&mut self, id: u64) -> bool {
        let index = self.ranges.partition_point(|range| *range.end() < id);
        let Some((start, end)) = self
            .ranges
            .get(index)
            .filter(|range| *range.start() <= id)
            .map(|range| (*range.start(), *range.end()))
        else {
            return false;
        };
        match (start == id, end == id) {
            (true, true) => {
                self.ranges.remove(index);
            }
            (true, false) => self.ranges[index] = id + 1..=end,
            (false, true) => self.ranges[index] = start..=id - 1,
            (false, false) => {
                self.ranges[index] = start..=id - 1;
                self.ranges.insert(index + 1, id + 1..=end);
            }
        }
        true
    }

    /// Removes every frame of `range`.
    pub fn remove_range(&mut self, range: &RangeInclusive<u64>) {
        let (low, high) = (*range.start(), *range.end());
        let mut kept = Vec::with_capacity(self.ranges.len() + 1);
        for existing in self.ranges.drain(..) {
            if *existing.end() < low || *existing.start() > high {
                kept.push(existing);
                continue;
            }
            if *existing.start() < low {
                kept.push(*existing.start()..=low - 1);
            }
            if *existing.end() > high {
                kept.push(high + 1..=*existing.end());
            }
        }
        self.ranges = kept;
    }

    /// Drops every member outside `window`.
    pub fn retain_within(&mut self, window: &RangeInclusive<u64>) {
        let (low, high) = (*window.start(), *window.end());
        self.ranges.retain_mut(|range| {
            if *range.end() < low || *range.start() > high {
                return false;
            }
            *range = (*range.start()).max(low)..=(*range.end()).min(high);
            true
        });
    }

    /// The maximal runs of `window` that are not in the set, in order.
    pub fn gaps_in(&self, window: RangeInclusive<u64>) -> Vec<RangeInclusive<u64>> {
        let (mut next, end) = (*window.start(), *window.end());
        let mut gaps = Vec::new();
        for range in &self.ranges {
            if next > end || *range.start() > end {
                break;
            }
            if *range.end() < next {
                continue;
            }
            if *range.start() > next {
                gaps.push(next..=*range.start() - 1);
            }
            match range.end().checked_add(1) {
                Some(after) => next = after,
                None => return gaps,
            }
        }
        if next <= end {
            gaps.push(next..=end);
        }
        gaps
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_operations_keep_ranges_sorted_disjoint_and_merged() {
        let mut set = FrameSet::from_ranges([10..=12, 0..=3, 5..=5]);
        assert_eq!(set.ranges(), [0..=3, 5..=5, 10..=12]);
        set.insert(4);
        set.insert_range(6..=9);
        assert_eq!(set, FrameSet::from(0..=12), "touching ranges merge");
        assert!(set.remove(6) && !set.remove(6));
        assert_eq!(set.ranges(), [0..=5, 7..=12]);
        assert_eq!(set.gaps_in(3..=14), [6..=6, 13..=14]);
        assert_eq!(set.gaps_in(7..=12), []);
        set.retain_within(&(4..=8));
        assert_eq!(set.ranges(), [4..=5, 7..=8]);
        set.remove_range(&(5..=7));
        assert_eq!(set.ranges(), [4..=4, 8..=8]);
        assert!(set.contains(4) && !set.contains(6) && !set.contains(9));
    }
}
