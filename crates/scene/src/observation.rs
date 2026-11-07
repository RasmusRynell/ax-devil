use std::cmp::Ordering;

use crate::{
    ClassType, Confidence, Fields, Geometry, Measurements, SceneError, SceneTime, Score, Value,
};

/// A class an observation was given.
///
/// Score and confidence are separate numbers and never derived from each other; a missing one stays `None`.
#[derive(Clone, Debug, PartialEq)]
pub struct Classification {
    /// The class, such as `car`, or `?N` for an unknown class id `N`.
    pub class_type: ClassType,
    /// How strongly the source rates the class, in the source's own scale.
    pub score: Option<Score>,
    /// How likely the source says the class is right.
    pub confidence: Option<Confidence>,
    /// Further facts about the class, such as a color, in the order they are shown.
    pub attributes: Fields,
}

impl Classification {
    /// A class with no score, confidence or attributes.
    pub fn new(class_type: ClassType) -> Self {
        Self {
            class_type,
            score: None,
            confidence: None,
            attributes: Fields::new(),
        }
    }

    /// The first attribute called `name`.
    pub fn attribute(&self, name: &str) -> Option<&Value> {
        self.attributes
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value)
    }
}

/// Orders by score, then confidence; a missing number ranks below any present one.
fn rank(a: &Classification, b: &Classification) -> Ordering {
    // Both numbers are finite, so the comparisons are total.
    let by_score = a.score.partial_cmp(&b.score).unwrap_or(Ordering::Equal);
    by_score.then_with(|| {
        a.confidence
            .partial_cmp(&b.confidence)
            .unwrap_or(Ordering::Equal)
    })
}

/// One sighting of an entity.
#[derive(Clone, Debug, PartialEq)]
pub struct Observation {
    /// When the sighting happened.
    pub time: SceneTime,
    /// Where the entity was.
    pub geometry: Geometry,
    /// What the entity was, in the order the source gave them.
    pub classifications: Vec<Classification>,
    /// What the source measured; rarely set, so it is boxed.
    pub measurements: Option<Box<Measurements>>,
    source_data: Fields,
    debug: Fields,
}

impl Observation {
    /// A sighting with no classes, measurements or details.
    pub fn new(time: SceneTime, geometry: Geometry) -> Self {
        Self {
            time,
            geometry,
            classifications: Vec::new(),
            measurements: None,
            source_data: Fields::new(),
            debug: Fields::new(),
        }
    }

    /// Sets the source's own data for the sighting and the decoder's diagnostics; no key may be in both.
    pub fn with_details(mut self, source_data: Fields, debug: Fields) -> Result<Self, SceneError> {
        if let Some((key, _)) = source_data
            .iter()
            .find(|(key, _)| debug.iter().any(|(other, _)| other == key))
        {
            return Err(SceneError::DetailKeyOverlap(key.clone()));
        }
        self.source_data = source_data;
        self.debug = debug;
        Ok(self)
    }

    /// The source's own data for this sighting, kept as it was.
    pub fn source_data(&self) -> &Fields {
        &self.source_data
    }

    /// Decoder diagnostics shown only in debug views.
    pub fn debug(&self) -> &Fields {
        &self.debug
    }

    /// The classifications best first: scored before unscored, then by score, then by confidence. Ties keep the
    /// source's order.
    pub fn ranked_classifications(&self) -> Vec<&Classification> {
        let mut ranked: Vec<_> = self.classifications.iter().collect();
        ranked.sort_by(|a, b| rank(b, a));
        ranked
    }

    /// The first of the best-ranked classifications.
    pub fn primary_classification(&self) -> Option<&Classification> {
        self.classifications
            .iter()
            .reduce(|best, next| if rank(next, best).is_gt() { next } else { best })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{BoundingBox, SceneTime};

    fn classification(
        name: &'static str,
        score: Option<f64>,
        confidence: Option<f64>,
    ) -> Classification {
        Classification {
            score: score.and_then(|s| Score::new(s).ok()),
            confidence: confidence.and_then(|c| Confidence::new(c).ok()),
            ..Classification::new(ClassType::from_static(name))
        }
    }

    fn ranked(classifications: Vec<Classification>) -> Vec<String> {
        let bounds = BoundingBox::new(0.1, 0.1, 0.3, 0.3).expect("a box");
        let mut observation = Observation::new(SceneTime::Frame(0), Geometry::Box(bounds));
        observation.classifications = classifications;
        let names =
            |list: Vec<&Classification>| list.iter().map(|c| c.class_type.to_string()).collect();
        let mut names: Vec<String> = names(observation.ranked_classifications());
        names.extend(
            observation
                .primary_classification()
                .map(|c| format!("primary {}", c.class_type)),
        );
        names
    }

    #[test]
    fn classes_rank_by_score_then_confidence_and_ties_keep_the_source_order() {
        let scored = vec![
            classification("a", None, Some(0.99)),
            classification("b", Some(0.1), None),
            classification("c", Some(0.8), None),
            classification("d", Some(0.8), None),
        ];
        assert_eq!(ranked(scored), ["c", "d", "b", "a", "primary c"]);

        let unscored = vec![
            classification("a", None, None),
            classification("b", None, Some(0.9)),
            classification("c", None, Some(0.2)),
        ];
        assert_eq!(ranked(unscored), ["b", "c", "a", "primary b"]);
        assert!(ranked(Vec::new()).is_empty());
    }

    #[test]
    fn source_data_and_debug_may_not_share_a_key() {
        let bounds = BoundingBox::new(0.0, 0.0, 1.0, 1.0).expect("a box");
        let observation = Observation::new(SceneTime::Frame(0), Geometry::Box(bounds));
        let fields = |key: &'static str| vec![(key.into(), Value::Null)];

        assert!(
            observation
                .clone()
                .with_details(fields("a"), fields("b"))
                .is_ok()
        );
        assert_eq!(
            observation.with_details(fields("a"), fields("a")),
            Err(SceneError::DetailKeyOverlap("a".into()))
        );
    }
}
