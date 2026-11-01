use crate::SceneError;

/// A point in coordinates normalized to the frame (0 to 1); values outside are kept.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    x: f64,
    y: f64,
}

impl Point {
    /// The point at `x`, `y`; both must be finite.
    pub fn new(x: f64, y: f64) -> Result<Self, SceneError> {
        for (what, value) in [("coordinate", x), ("coordinate", y)] {
            if !value.is_finite() {
                return Err(SceneError::NonFinite { what, value });
            }
        }
        Ok(Self { x, y })
    }

    /// Distance from the left edge.
    pub fn x(&self) -> f64 {
        self.x
    }

    /// Distance from the top edge.
    pub fn y(&self) -> f64 {
        self.y
    }
}

/// An axis-aligned box in normalized coordinates.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BoundingBox {
    top_left: Point,
    bottom_right: Point,
}

impl BoundingBox {
    /// The box with these edges; all must be finite, `right` not below `left` and `bottom` not above `top`.
    pub fn new(left: f64, top: f64, right: f64, bottom: f64) -> Result<Self, SceneError> {
        let (top_left, bottom_right) = (Point::new(left, top)?, Point::new(right, bottom)?);
        if right < left || bottom < top {
            return Err(SceneError::InvertedBox);
        }
        Ok(Self {
            top_left,
            bottom_right,
        })
    }

    /// The left edge.
    pub fn left(&self) -> f64 {
        self.top_left.x
    }

    /// The top edge.
    pub fn top(&self) -> f64 {
        self.top_left.y
    }

    /// The right edge.
    pub fn right(&self) -> f64 {
        self.bottom_right.x
    }

    /// The bottom edge.
    pub fn bottom(&self) -> f64 {
        self.bottom_right.y
    }

    /// Right minus left.
    pub fn width(&self) -> f64 {
        self.right() - self.left()
    }

    /// Bottom minus top.
    pub fn height(&self) -> f64 {
        self.bottom() - self.top()
    }

    /// Width times height.
    pub fn area(&self) -> f64 {
        self.width() * self.height()
    }
}

/// A closed outline of at least three points.
#[derive(Clone, Debug, PartialEq)]
pub struct Polygon(Box<[Point]>);

impl Polygon {
    /// The polygon through `points`, which must be at least three.
    pub fn new(points: Vec<Point>) -> Result<Self, SceneError> {
        if points.len() < 3 {
            return Err(SceneError::TooFewPolygonPoints(points.len()));
        }
        Ok(Self(points.into_boxed_slice()))
    }

    /// The outline's points in order.
    pub fn points(&self) -> &[Point] {
        &self.0
    }
}

/// Where something was.
#[derive(Clone, Debug, PartialEq)]
pub enum Geometry {
    /// An axis-aligned box.
    Box(BoundingBox),
    /// A closed outline.
    Polygon(Polygon),
}

impl Geometry {
    /// The smallest box that holds the geometry.
    pub fn bounds(&self) -> BoundingBox {
        match self {
            Self::Box(bounds) => *bounds,
            Self::Polygon(polygon) => {
                let first = polygon.0[0];
                let (mut top_left, mut bottom_right) = (first, first);
                for point in &polygon.0[1..] {
                    top_left.x = top_left.x.min(point.x);
                    top_left.y = top_left.y.min(point.y);
                    bottom_right.x = bottom_right.x.max(point.x);
                    bottom_right.y = bottom_right.y.max(point.y);
                }
                BoundingBox {
                    top_left,
                    bottom_right,
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn boxes_are_finite_and_ordered_and_polygons_have_three_points() {
        assert!(
            BoundingBox::new(-0.1, 0.0, 1.2, 0.0).is_ok(),
            "outside 0..1 and flat boxes are kept"
        );
        assert_eq!(
            BoundingBox::new(0.5, 0.0, 0.4, 1.0),
            Err(SceneError::InvertedBox)
        );
        assert_eq!(
            BoundingBox::new(0.0, 0.5, 1.0, 0.4),
            Err(SceneError::InvertedBox)
        );
        assert!(matches!(
            BoundingBox::new(0.0, f64::NAN, 1.0, 1.0),
            Err(SceneError::NonFinite { .. })
        ));

        let point = |x, y| Point::new(x, y).expect("finite");
        let line = vec![point(0.0, 0.0), point(1.0, 1.0)];
        assert_eq!(Polygon::new(line), Err(SceneError::TooFewPolygonPoints(2)));
    }

    #[test]
    fn a_polygon_is_bounded_by_its_extreme_points() {
        let points = [(0.2, 0.9), (0.6, 0.1), (0.4, 0.5)]
            .map(|(x, y)| Point::new(x, y).expect("finite"))
            .to_vec();
        let polygon = Polygon::new(points).expect("three points");

        let bounds = Geometry::Polygon(polygon).bounds();

        assert_eq!(
            bounds,
            BoundingBox::new(0.2, 0.1, 0.6, 0.9).expect("ordered")
        );
    }
}
