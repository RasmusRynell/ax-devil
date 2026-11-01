//! The Drawing Instruction contract: what a lane's overlay looks like for one frame, with no knowledge of Scenes.
//!
//! All coordinates are normalized [0,1] of the frame. Widths, radii, font sizes, paddings and gaps are relative to
//! min(target width, height). Rows reference the shared paint, label style and string tables by index, so shared
//! paint stays shared. Draw order is shapes, then texts, then labels; order within a kind carries no meaning.

mod canonical;
mod strings;

pub use strings::{StrId, Strings};

/// How a shape or text is drawn. `font_family` is [`StrId::NONE`] for the default family.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Paint {
    /// Outline color, red green blue.
    pub pen: [u8; 3],
    /// Whether the outline is dashed.
    pub dashed: bool,
    /// Outline width.
    pub pen_width: f32,
    /// Fill color, red green blue alpha; alpha 0 means no fill.
    pub brush: [u8; 4],
    /// Font family of a text.
    pub font_family: StrId,
    /// Font size of a text.
    pub font_size: f32,
    /// Corner radius of a box.
    pub radius: f32,
}

/// The point of a text or label rectangle that sits at the requested position.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Anchor {
    /// Top left corner.
    TopLeft,
    /// Middle of the top edge.
    TopCenter,
    /// Top right corner.
    TopRight,
    /// Middle of the left edge.
    CenterLeft,
    /// Center.
    Center,
    /// Middle of the right edge.
    CenterRight,
    /// Bottom left corner.
    BottomLeft,
    /// Middle of the bottom edge.
    BottomCenter,
    /// Bottom right corner.
    BottomRight,
    /// Same as `TopCenter`.
    Top,
    /// Same as `BottomCenter`.
    Bottom,
    /// Same as `CenterLeft`.
    Left,
    /// Same as `CenterRight`.
    Right,
}

impl Anchor {
    /// Every anchor with its catalog name, in wire order.
    pub const NAMES: [(Anchor, &'static str); 13] = [
        (Anchor::TopLeft, "top-left"),
        (Anchor::TopCenter, "top-center"),
        (Anchor::TopRight, "top-right"),
        (Anchor::CenterLeft, "center-left"),
        (Anchor::Center, "center"),
        (Anchor::CenterRight, "center-right"),
        (Anchor::BottomLeft, "bottom-left"),
        (Anchor::BottomCenter, "bottom-center"),
        (Anchor::BottomRight, "bottom-right"),
        (Anchor::Top, "top"),
        (Anchor::Bottom, "bottom"),
        (Anchor::Left, "left"),
        (Anchor::Right, "right"),
    ];

    /// The anchor with this catalog name, such as `top-left`.
    pub fn parse(name: &str) -> Option<Self> {
        Self::NAMES
            .iter()
            .find(|(_, candidate)| *candidate == name)
            .map(|(anchor, _)| *anchor)
    }

    /// The catalog name.
    pub fn name(self) -> &'static str {
        Self::NAMES[self as usize].1
    }
}

/// How heavy a label run's text is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Weight {
    /// Normal.
    Regular,
    /// Slightly heavier.
    Medium,
    /// Heavier.
    Semibold,
    /// Heaviest.
    Bold,
}

impl Weight {
    /// The catalog name, such as `semibold`.
    pub fn name(self) -> &'static str {
        match self {
            Self::Regular => "regular",
            Self::Medium => "medium",
            Self::Semibold => "semibold",
            Self::Bold => "bold",
        }
    }
}

/// A rectangle outline and fill.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BoxRow {
    /// Left edge.
    pub x: f32,
    /// Top edge.
    pub y: f32,
    /// Width.
    pub w: f32,
    /// Height.
    pub h: f32,
    /// Index into `paints`.
    pub paint: u32,
}

/// A straight line with square caps.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LineRow {
    /// Start, across.
    pub x1: f32,
    /// Start, down.
    pub y1: f32,
    /// End, across.
    pub x2: f32,
    /// End, down.
    pub y2: f32,
    /// Index into `paints`.
    pub paint: u32,
}

/// A circle.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CircleRow {
    /// Center, across.
    pub x: f32,
    /// Center, down.
    pub y: f32,
    /// Radius.
    pub radius: f32,
    /// Index into `paints`.
    pub paint: u32,
}

/// A point.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PointRow {
    /// Across.
    pub x: f32,
    /// Down.
    pub y: f32,
    /// Index into `paints`.
    pub paint: u32,
}

/// A polygon or polyline: a run of `path_points`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PathRow {
    /// Index of the first point in `path_points`.
    pub first: u32,
    /// Number of points.
    pub count: u32,
    /// Index into `paints`.
    pub paint: u32,
}

/// A text placed by its anchor.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TextRow {
    /// Position, across.
    pub x: f32,
    /// Position, down.
    pub y: f32,
    /// The text, in `strings`.
    pub text: StrId,
    /// Which point of the text sits at the position.
    pub anchor: Anchor,
    /// Index into `paints`.
    pub paint: u32,
}

/// How a label looks, shared by the labels that use it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LabelStyle {
    /// Font size.
    pub size: f32,
    /// Font family; [`StrId::NONE`] for the default.
    pub family: StrId,
    /// Background color, red green blue alpha; alpha 0 means none.
    pub background: [u8; 4],
    /// Space left and right of the runs.
    pub padding_x: f32,
    /// Space above and below the runs.
    pub padding_y: f32,
    /// Background corner radius.
    pub radius: f32,
    /// Space between runs.
    pub gap: f32,
}

/// A row of runs on a background, placed by its anchor.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LabelRow {
    /// Position, across.
    pub x: f32,
    /// Position, down.
    pub y: f32,
    /// Which point of the label sits at the position.
    pub anchor: Anchor,
    /// Index into `label_styles`.
    pub style: u32,
    /// Index of the first run in `runs`.
    pub first_run: u32,
    /// Number of runs.
    pub runs: u32,
}

/// One piece of a label: a text, or a meter bar when `bar` is a number.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RunRow {
    /// The text, in `strings`; unused for a bar.
    pub text: StrId,
    /// NaN for a text run; else the bar fill from 0 to 1, in hundredths.
    pub bar: f32,
    /// Text or bar color.
    pub rgb: [u8; 3],
    /// Text weight.
    pub weight: Weight,
}

/// One lane frame's Drawing Instructions. Immutable once built; share it as an `Arc`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct DrawingInstructions {
    /// Shared paints.
    pub paints: Vec<Paint>,
    /// Rectangles.
    pub boxes: Vec<BoxRow>,
    /// Lines.
    pub lines: Vec<LineRow>,
    /// Circles.
    pub circles: Vec<CircleRow>,
    /// Points.
    pub points: Vec<PointRow>,
    /// The points of every polygon and polyline.
    pub path_points: Vec<[f32; 2]>,
    /// Closed polygons, filled odd-even.
    pub polygons: Vec<PathRow>,
    /// Open polylines with round joins.
    pub polylines: Vec<PathRow>,
    /// Texts.
    pub texts: Vec<TextRow>,
    /// Shared label styles.
    pub label_styles: Vec<LabelStyle>,
    /// Labels.
    pub labels: Vec<LabelRow>,
    /// The runs of every label.
    pub runs: Vec<RunRow>,
    /// Every string the rows use.
    pub strings: Strings,
}

impl DrawingInstructions {
    /// The index of `paint` in `paints`, adding it when no equal paint is there.
    pub fn paint(&mut self, paint: Paint) -> u32 {
        let index = self
            .paints
            .iter()
            .position(|existing| *existing == paint)
            .unwrap_or_else(|| {
                self.paints.push(paint);
                self.paints.len() - 1
            });
        u32::try_from(index).unwrap_or(u32::MAX)
    }

    /// Whether nothing is drawn.
    pub fn is_empty(&self) -> bool {
        self.boxes.is_empty()
            && self.lines.is_empty()
            && self.circles.is_empty()
            && self.points.is_empty()
            && self.polygons.is_empty()
            && self.polylines.is_empty()
            && self.texts.is_empty()
            && self.labels.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn paint(width: f32) -> Paint {
        Paint {
            pen: [0, 255, 0],
            dashed: false,
            pen_width: width,
            brush: [0; 4],
            font_family: StrId::NONE,
            font_size: 0.02,
            radius: 0.0,
        }
    }

    #[test]
    fn equal_paints_share_one_index_and_anchors_round_trip_their_names() {
        let mut drawing = DrawingInstructions::default();
        let first = drawing.paint(paint(0.002));
        let other = drawing.paint(paint(0.004));
        assert_eq!((first, other, drawing.paint(paint(0.002))), (0, 1, 0));
        assert_eq!(drawing.paints.len(), 2);
        assert!(drawing.is_empty());

        for (anchor, name) in Anchor::NAMES {
            assert_eq!(Anchor::parse(name), Some(anchor));
            assert_eq!(anchor.name(), name);
        }
        assert_eq!(Anchor::parse("middle"), None);
    }
}
