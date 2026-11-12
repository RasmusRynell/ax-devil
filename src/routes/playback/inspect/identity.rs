//! Socket-local inspection identity, shared by reads and release on offline and live streams.
use super::Inspections;
use api_types::inspect::InspectionRelease;

impl Inspections {
    /// Identifies the target only for this socket's current capability. The caller rechecks its held sample.
    pub(super) fn continuation_target(&self, token: &str) -> Option<(u16, u32, u32)> {
        let (held, source, revision, sample, _, _, _) = self.cursor.as_ref()?;
        (token == held).then_some((*source, *revision, *sample))
    }

    /// Whether this is a retransmission of the current initial request.
    pub(super) fn repeated(
        &self,
        generation: u32,
        source: u16,
        revision: u32,
        sample: u32,
        entity: &str,
    ) -> bool {
        self.cursor.as_ref().is_some_and(|c| {
            c.6 == generation && c.1 == source && c.2 == revision && c.3 == sample && c.4 == entity
        })
    }

    /// Releases only the inspection named by its token or initial request generation; stale releases do nothing.
    pub(in crate::routes::playback) fn release_read(&mut self, identity: &InspectionRelease) {
        let current = match identity {
            InspectionRelease::Token { token } => self.continuation_target(token).is_some(),
            InspectionRelease::Request { request } => {
                self.cursor.as_ref().is_some_and(|c| c.6 == *request)
            }
        };
        if current {
            self.release();
        }
    }
}
