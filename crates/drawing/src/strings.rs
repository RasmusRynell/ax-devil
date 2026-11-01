use std::collections::HashMap;

/// A string in [`Strings`]; [`StrId::NONE`] is no string.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct StrId(pub u32);

impl StrId {
    /// No string.
    pub const NONE: StrId = StrId(u32::MAX);
}

/// Deduplicated strings kept as one UTF-8 blob and the offsets between them.
#[derive(Clone, Debug)]
pub struct Strings {
    blob: String,
    /// `offsets[i]..offsets[i + 1]` is string `i`; starts with 0.
    offsets: Vec<u32>,
    index: HashMap<Box<str>, u32>,
}

impl Default for Strings {
    fn default() -> Self {
        Self {
            blob: String::new(),
            offsets: vec![0],
            index: HashMap::new(),
        }
    }
}

impl PartialEq for Strings {
    fn eq(&self, other: &Self) -> bool {
        self.blob == other.blob && self.offsets == other.offsets
    }
}

impl Strings {
    /// The id of `text`, adding it when it is new.
    pub fn intern(&mut self, text: &str) -> StrId {
        if let Some(&id) = self.index.get(text) {
            return StrId(id);
        }
        let id = u32::try_from(self.index.len()).unwrap_or(u32::MAX - 1);
        self.blob.push_str(text);
        self.offsets
            .push(u32::try_from(self.blob.len()).unwrap_or(u32::MAX));
        self.index.insert(text.into(), id);
        StrId(id)
    }

    /// The string, or `None` for [`StrId::NONE`] and ids that were never handed out.
    pub fn get(&self, id: StrId) -> Option<&str> {
        let index = usize::try_from(id.0).ok()?;
        let start = usize::try_from(*self.offsets.get(index)?).ok()?;
        let end = usize::try_from(*self.offsets.get(index + 1)?).ok()?;
        self.blob.get(start..end)
    }

    /// Every string end to end.
    pub fn blob(&self) -> &str {
        &self.blob
    }

    /// The offsets into [`Strings::blob`]: one more than there are strings, the first is 0.
    pub fn offsets(&self) -> &[u32] {
        &self.offsets
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strings_are_stored_once_and_found_by_id() {
        let mut strings = Strings::default();
        let car = strings.intern("car");
        let empty = strings.intern("");
        let person = strings.intern("person");

        assert_eq!(strings.intern("car"), car);
        assert_eq!((car, empty, person), (StrId(0), StrId(1), StrId(2)));
        assert_eq!(strings.blob(), "carperson");
        assert_eq!(strings.offsets(), [0, 3, 3, 9]);
        assert_eq!(strings.get(person), Some("person"));
        assert_eq!(strings.get(empty), Some(""));
        assert_eq!(strings.get(StrId::NONE), None);
        assert_eq!(strings.get(StrId(3)), None);
    }
}
