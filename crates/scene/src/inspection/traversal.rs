//! Per-capability classification order and bounded colour-list recognition.
use super::{Entity, FieldChunk, FieldPage};
use std::cmp::Ordering;

/// The largest classification permutation: 2,097,152 u32 indices, exactly 8 MiB.
pub const MAX_CLASSIFICATIONS: usize = 2 << 20;

/// Stable classification order prepared once for one inspected entity of a held sample.
pub struct Traversal {
    order: Vec<u32>,
    cancel: foundation::CancelToken,
    shapes: std::sync::Mutex<super::list_shape::ListShapes>,
}
impl Traversal {
    /// Prepares an index permutation; refuses before allocating when the classification count exceeds the cap.
    pub fn new(entity: &Entity, cancel: foundation::CancelToken) -> Result<Self, String> {
        cancel
            .check()
            .map_err(|_| "Inspection cancelled".to_owned())?;
        let classes = entity.latest_observation().map_or(&[][..], |observation| {
            observation.classifications.as_slice()
        });
        if classes.len() > MAX_CLASSIFICATIONS {
            return Err(format!(
                "Object has {} classifications; inspection supports at most {MAX_CLASSIFICATIONS}",
                classes.len()
            ));
        }
        let mut order = Vec::with_capacity(classes.len());
        for index in 0..classes.len() as u32 {
            cancel
                .check()
                .map_err(|_| "Inspection cancelled".to_owned())?;
            order.push(index);
        }
        sort_order(&mut order, &cancel, |a, b| {
            let (left, right) = (&classes[a as usize], &classes[b as usize]);
            right
                .score
                .partial_cmp(&left.score)
                .unwrap_or(Ordering::Equal)
                .then_with(|| {
                    right
                        .confidence
                        .partial_cmp(&left.confidence)
                        .unwrap_or(Ordering::Equal)
                })
                .then_with(|| a.cmp(&b))
        })?;
        cancel
            .check()
            .map_err(|_| "Inspection cancelled".to_owned())?;
        Ok(Self {
            order,
            cancel,
            shapes: std::sync::Mutex::default(),
        })
    }

    /// Whether the capability owning this traversal was released.
    pub fn cancelled(&self) -> bool {
        self.cancel.is_cancelled()
    }

    pub(super) fn order(&self) -> &[u32] {
        &self.order
    }
    pub(super) fn colors(
        &self,
        address: &[usize],
        items: &[crate::Value],
        work: &mut usize,
    ) -> Option<bool> {
        // The scan shares its page work budget. Playback runs it on a blocking worker; socket capability
        // release never takes this separate shape-cache lock.
        self.shapes
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .check(address, items, work, &self.cancel)
    }

    /// Resumes a bounded page at an address returned with the preceding page.
    pub fn page(&self, entity: &Entity, start: usize, address: &[usize]) -> FieldPage {
        super::pages::page_ordered(entity, start, address, self, false)
    }

    /// Re-reads one field at its retained address with a 16 KiB text and 64 KiB path preview.
    /// An invalid address returns no preview rather than the next field in traversal order.
    pub fn preview(
        &self,
        entity: &Entity,
        ordinal: usize,
        address: &[usize],
    ) -> Option<super::PageField> {
        super::pages::page_ordered(entity, ordinal, address, self, true)
            .fields
            .into_iter()
            .find(|field| field.address == address)
    }

    /// Reads one chunk directly at an address of the current page.
    pub fn chunk(
        &self,
        entity: &Entity,
        ordinal: usize,
        offset: usize,
        path: bool,
        address: &[usize],
    ) -> Result<FieldChunk, String> {
        super::pages::chunk_ordered(entity, ordinal, offset, path, address, self)
    }
}

// In-place heapsort can stop at every sift step without changing the comparison relation or allocating a
// second permutation. Index ties retain source order, just as in the normal classification ranking.
fn sort_order(
    order: &mut [u32],
    cancel: &foundation::CancelToken,
    compare: impl Fn(u32, u32) -> Ordering,
) -> Result<(), String> {
    let sift = |order: &mut [u32], mut root: usize, end: usize| -> Result<(), String> {
        while root * 2 + 1 < end {
            cancel
                .check()
                .map_err(|_| "Inspection cancelled".to_owned())?;
            let mut child = root * 2 + 1;
            if child + 1 < end && compare(order[child], order[child + 1]).is_lt() {
                child += 1;
            }
            if !compare(order[root], order[child]).is_lt() {
                break;
            }
            order.swap(root, child);
            root = child;
        }
        Ok(())
    };
    for root in (0..order.len() / 2).rev() {
        sift(order, root, order.len())?;
    }
    for end in (1..order.len()).rev() {
        cancel
            .check()
            .map_err(|_| "Inspection cancelled".to_owned())?;
        order.swap(0, end);
        sift(order, 0, end)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        BoundingBox, ClassType, Classification, EntityId, Geometry, Observation, SceneTime,
    };

    #[test]
    fn classification_preparation_keeps_at_most_eight_mib_and_refuses_before_allocating_more() {
        for count in [2_097_152, 2_097_153] {
            let mut observation = Observation::new(
                SceneTime::Frame(0),
                Geometry::Box(BoundingBox::new(0., 0., 1., 1.).unwrap()),
            );
            observation.classifications = vec![Classification::new(ClassType::CAR); count];
            let mut entity = Entity::new(EntityId::from(7_i64));
            entity.push_observation(observation);
            let result = Traversal::new(&entity, foundation::CancelToken::new());
            if count == 2_097_152 {
                let prepared = result.unwrap();
                assert_eq!(
                    prepared.order.capacity() * std::mem::size_of::<u32>(),
                    8 * 1024 * 1024
                );
            } else {
                assert_eq!(
                    result.err().as_deref(),
                    Some("Object has 2097153 classifications; inspection supports at most 2097152")
                );
            }
        }
    }
    #[test]
    fn pages_resume_across_a_large_empty_prefix_without_revisiting_it() {
        use crate::Value;
        let mut observation = Observation::new(
            SceneTime::Frame(0),
            Geometry::Box(BoundingBox::new(0., 0., 1., 1.).unwrap()),
        );
        let mut class = Classification::new(ClassType::CAR);
        class.attributes = vec![
            ("empty".into(), Value::List(vec![Value::Null; 2_100_000])),
            ("last".into(), Value::Str("reached".into())),
        ];
        observation.classifications = vec![class];
        let mut entity = Entity::new(EntityId::from(7_i64));
        entity.push_observation(observation);
        let traversal = Traversal::new(&entity, foundation::CancelToken::new()).unwrap();
        let first = traversal.page(&entity, 0, &[]);
        assert_eq!(first.fields.len(), 1);
        let second = traversal.page(
            &entity,
            first.next.unwrap(),
            first.next_address.as_deref().unwrap(),
        );
        assert!(
            second.fields.is_empty(),
            "work remains bounded even when no field can be emitted"
        );
        assert_ne!(
            first.next_address, second.next_address,
            "the structural cursor advances without an emitted field"
        );
        let third = traversal.page(
            &entity,
            second.next.unwrap(),
            second.next_address.as_deref().unwrap(),
        );
        assert_eq!(third.fields[0].field.path, ["attributes", "last"]);
        assert_eq!(third.next, None);
    }

    #[test]
    fn color_lists_keep_their_shape_and_order_across_pages_and_direct_path_reads() {
        use crate::Value;
        let mut observation = Observation::new(
            SceneTime::Frame(0),
            Geometry::Box(BoundingBox::new(0., 0., 1., 1.).unwrap()),
        );
        let mut class = Classification::new(ClassType::CAR);
        class.attributes = vec![(
            "colors".into(),
            Value::List(
                (0..10000)
                    .map(|index| {
                        Value::Map(vec![
                            ("rgb".into(), Value::List(vec![Value::Int(0); 3])),
                            ("score".into(), Value::Float(f64::from(index))),
                        ])
                    })
                    .collect(),
            ),
        )];
        observation.classifications = vec![class];
        let mut entity = Entity::new(EntityId::from(7_i64));
        entity.push_observation(observation);
        let traversal = Traversal::new(&entity, foundation::CancelToken::new()).unwrap();
        let mut next = 0;
        let mut address = Vec::new();
        let mut seen = Vec::new();
        loop {
            let page = traversal.page(&entity, next, &address);
            for (index, field) in page.fields.iter().enumerate() {
                if field.field.color.is_some() {
                    seen.push(field.field.value.clone());
                    let path = traversal
                        .chunk(&entity, next + index, 0, true, &field.address)
                        .unwrap();
                    assert_eq!(path.text, field.field.path.join("."));
                }
            }
            if let Some(ordinal) = page.next {
                next = ordinal;
                address = page.next_address.unwrap();
            } else {
                break;
            }
        }
        assert_eq!(
            seen,
            (0..10000)
                .map(|i| super::super::Leaf::Float(f64::from(i)))
                .collect::<Vec<_>>()
        );
    }
    #[test]
    fn the_cached_candidate_order_uses_score_then_confidence_and_preserves_source_ties() {
        use crate::{Confidence, Score};
        let mut observation = Observation::new(
            SceneTime::Frame(0),
            Geometry::Box(BoundingBox::new(0., 0., 1., 1.).unwrap()),
        );
        observation.classifications = [
            ("a", Some(0.3), None),
            ("b", None, None),
            ("c", Some(0.3), Some(0.9)),
            ("d", Some(0.8), None),
            ("e", Some(0.3), None),
        ]
        .into_iter()
        .map(|(name, score, confidence)| {
            let mut class = Classification::new(ClassType::new(name).unwrap());
            class.score = score.map(|v| Score::new(v).unwrap());
            class.confidence = confidence.map(|v| Confidence::new(v).unwrap());
            class
        })
        .collect();
        let mut entity = Entity::new(EntityId::from(7_i64));
        entity.push_observation(observation);
        let traversal = Traversal::new(&entity, foundation::CancelToken::new()).unwrap();
        let page = traversal.page(&entity, 0, &[]);
        assert_eq!(
            page.fields[0].field.value,
            super::super::Leaf::Text("d".into())
        );
        let mut candidates = Vec::new();
        for field in page.fields {
            if field
                .field
                .path
                .first()
                .is_some_and(|part| part == "candidates")
            {
                let name = field.field.path[1].clone();
                if candidates.last() != Some(&name) {
                    candidates.push(name);
                }
            }
        }
        // A classification without a score or confidence has no candidate row; it still participated in preparation.
        assert_eq!(candidates, ["d", "c", "a", "e"]);
    }
}
