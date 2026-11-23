//! Ascending keys stored in shared chunks, so a growing list is published without copying all of it.

use std::sync::Arc;

/// How many keys a sealed chunk holds.
pub const CHUNK_KEYS: usize = 65_536;

/// Ascending keys in sealed chunks of [`CHUNK_KEYS`] plus a shorter tail.
///
/// Cloning shares the chunks. [`Self::appended`] makes the longer list by copying at most the tail and the new keys,
/// so a reader keeps its list while a writer publishes the next one. The keys must ascend; the type does not check.
#[derive(Clone, Debug)]
pub struct KeyChunks<T> {
    sealed: Vec<Arc<[T]>>,
    tail: Arc<[T]>,
}

impl<T: Copy> KeyChunks<T> {
    /// No keys.
    pub fn new() -> Self {
        Self {
            sealed: Vec::new(),
            tail: Arc::from([]),
        }
    }

    /// How many keys there are.
    pub fn len(&self) -> usize {
        self.sealed.len() * CHUNK_KEYS + self.tail.len()
    }

    /// Whether there are no keys.
    pub fn is_empty(&self) -> bool {
        self.sealed.is_empty() && self.tail.is_empty()
    }

    /// The key at `index`.
    pub fn get(&self, index: usize) -> Option<T> {
        match self.sealed.get(index / CHUNK_KEYS) {
            Some(chunk) => Some(chunk[index % CHUNK_KEYS]),
            None => self
                .tail
                .get(index - self.sealed.len() * CHUNK_KEYS)
                .copied(),
        }
    }

    /// The number of leading keys for which `keep` holds, which it must for a prefix only (as
    /// [`slice::partition_point`]).
    pub fn partition_point(&self, mut keep: impl FnMut(&T) -> bool) -> usize {
        // Sealed chunks are full and non-empty; the first whose last key fails `keep` holds the boundary.
        let chunk = self
            .sealed
            .partition_point(|chunk| keep(&chunk[CHUNK_KEYS - 1]));
        match self.sealed.get(chunk) {
            Some(found) => chunk * CHUNK_KEYS + found.partition_point(keep),
            None => self.sealed.len() * CHUNK_KEYS + self.tail.partition_point(keep),
        }
    }

    /// The keys in order.
    pub fn iter(&self) -> impl Iterator<Item = T> + '_ {
        self.sealed
            .iter()
            .flat_map(|chunk| chunk.iter().copied())
            .chain(self.tail.iter().copied())
    }

    /// These keys followed by `more`.
    #[must_use]
    pub fn appended(&self, more: &[T]) -> Self {
        let mut sealed = self.sealed.clone();
        let mut rest = Vec::with_capacity(self.tail.len() + more.len());
        rest.extend_from_slice(&self.tail);
        rest.extend_from_slice(more);
        while rest.len() >= CHUNK_KEYS {
            let later = rest.split_off(CHUNK_KEYS);
            sealed.push(Arc::from(std::mem::replace(&mut rest, later)));
        }
        Self {
            sealed,
            tail: Arc::from(rest),
        }
    }
}

impl<T: Copy> Default for KeyChunks<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Copy + PartialEq> PartialEq for KeyChunks<T> {
    fn eq(&self, other: &Self) -> bool {
        self.len() == other.len() && self.iter().eq(other.iter())
    }
}

impl<T: Copy + Eq> Eq for KeyChunks<T> {}

impl<T: Copy> From<&[T]> for KeyChunks<T> {
    fn from(keys: &[T]) -> Self {
        Self::new().appended(keys)
    }
}

impl<T: Copy> From<Vec<T>> for KeyChunks<T> {
    fn from(keys: Vec<T>) -> Self {
        Self::from(keys.as_slice())
    }
}

impl<T: Copy> FromIterator<T> for KeyChunks<T> {
    fn from_iter<I: IntoIterator<Item = T>>(keys: I) -> Self {
        Self::from(keys.into_iter().collect::<Vec<_>>())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appended_lists_search_and_index_across_chunk_edges() {
        let all: Vec<u64> = (0..(2 * CHUNK_KEYS as u64 + 10)).map(|n| n * 2).collect();
        // Built in uneven steps, so chunks seal at different moments.
        let mut keys = KeyChunks::new();
        for step in all.chunks(CHUNK_KEYS / 3 + 7) {
            let before = keys.clone();
            keys = keys.appended(step);
            assert_eq!(
                before.len() + step.len(),
                keys.len(),
                "a published list keeps its keys"
            );
        }

        assert_eq!(keys, KeyChunks::from(all.clone()));
        for probe in [
            0,
            1,
            CHUNK_KEYS - 1,
            CHUNK_KEYS,
            CHUNK_KEYS + 1,
            all.len() - 1,
            all.len(),
        ] {
            assert_eq!(keys.get(probe), all.get(probe).copied());
            let below = probe as u64 * 2;
            assert_eq!(
                keys.partition_point(|&key| key < below),
                all.partition_point(|&key| key < below),
                "keys below {below}"
            );
        }
        assert_eq!(keys.iter().collect::<Vec<_>>(), all);
    }
}
