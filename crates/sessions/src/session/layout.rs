//! How a window lays its viewers out: a binary tree of panes, kept in the [`View`](super::View) so a following window
//! shows the presenter's split.

use serde::{
    Deserialize, Deserializer, Serialize,
    de::{self, Visitor},
};
use std::{collections::BTreeSet, fmt};
use ts_rs::TS;

/// The most panes a window lays out; a [`Layout`] with more leaves is refused. A seat may hold at most as many
/// streams.
pub const MAX_PANES: usize = 8;

/// A share is the first child's part of its split, in ten-thousandths.
const SHARE_WHOLE: u16 = 10_000;

/// Names one viewer in a window's view. The window chooses it, one above the highest it has, and the backend never
/// reuses or invents one. Two viewers may show the same item.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct ViewerId(pub u32);

/// Reads the number, or the text of one: JSON writes a map key as text, and a message that buffers its content
/// first (an enum tagged by field) hands the key on as text.
impl<'de> Deserialize<'de> for ViewerId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Id;
        impl Visitor<'_> for Id {
            type Value = ViewerId;

            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a viewer id")
            }

            fn visit_u64<E: de::Error>(self, id: u64) -> Result<ViewerId, E> {
                u32::try_from(id).map(ViewerId).map_err(E::custom)
            }

            fn visit_str<E: de::Error>(self, id: &str) -> Result<ViewerId, E> {
                id.parse().map(ViewerId).map_err(E::custom)
            }
        }
        deserializer.deserialize_any(Id)
    }
}

/// Which way a split divides its space.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum Axis {
    /// The children sit side by side.
    Row,
    /// The children sit one above the other.
    Column,
}

/// Where each viewer is: a pane, or a split of two layouts.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum Layout {
    /// One viewer fills the space.
    Pane {
        /// The viewer.
        #[ts(type = "number")]
        viewer: ViewerId,
    },
    /// Two layouts share the space.
    Split {
        /// Which way it divides.
        axis: Axis,
        /// The first child's part, from 1 to 9999 of every 10000.
        share: u16,
        /// The left or upper child.
        first: Box<Layout>,
        /// The right or lower child.
        second: Box<Layout>,
    },
}

impl Layout {
    /// The viewers in the panes, in layout order.
    pub fn viewers(&self) -> Vec<ViewerId> {
        match self {
            Self::Pane { viewer } => vec![*viewer],
            Self::Split { first, second, .. } => {
                let mut viewers = first.viewers();
                viewers.extend(second.viewers());
                viewers
            }
        }
    }

    /// Whether this is a layout a window may show: at most [`MAX_PANES`] panes, no viewer in two of them, and every
    /// share inside its range.
    pub fn is_valid(&self) -> bool {
        let viewers = self.viewers();
        viewers.len() <= MAX_PANES
            && viewers.iter().collect::<BTreeSet<_>>().len() == viewers.len()
            && self.shares_valid()
    }

    fn shares_valid(&self) -> bool {
        match self {
            Self::Pane { .. } => true,
            Self::Split {
                share,
                first,
                second,
                ..
            } => (1..SHARE_WHOLE).contains(share) && first.shares_valid() && second.shares_valid(),
        }
    }

    /// This layout without the panes `keep` rejects. A split that loses one child becomes the other, which keeps its
    /// own shares; `None` when no pane is left.
    pub fn retain(self, keep: &impl Fn(ViewerId) -> bool) -> Option<Self> {
        match self {
            Self::Pane { viewer } => keep(viewer).then_some(self),
            Self::Split {
                axis,
                share,
                first,
                second,
            } => match (first.retain(keep), second.retain(keep)) {
                (Some(first), Some(second)) => Some(Self::Split {
                    axis,
                    share,
                    first: Box::new(first),
                    second: Box::new(second),
                }),
                (kept, other) => kept.or(other),
            },
        }
    }
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<Axis>();
    types.visit::<Layout>();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pane(viewer: u32) -> Box<Layout> {
        Box::new(Layout::Pane {
            viewer: ViewerId(viewer),
        })
    }

    fn split(share: u16, first: Box<Layout>, second: Box<Layout>) -> Box<Layout> {
        Box::new(Layout::Split {
            axis: Axis::Row,
            share,
            first,
            second,
        })
    }

    #[test]
    fn pruning_a_pane_hands_its_space_to_the_sibling_and_keeps_the_other_shares() {
        let tree = split(3000, pane(1), split(7000, pane(2), pane(3)));
        let without = |gone: u32| (*tree.clone()).retain(&|viewer| viewer != ViewerId(gone));
        assert_eq!(without(1), Some(*split(7000, pane(2), pane(3))));
        assert_eq!(without(3), Some(*split(3000, pane(1), pane(2))));
        assert_eq!(without(2), Some(*split(3000, pane(1), pane(3))));
        assert_eq!((*tree).retain(&|_| false), None);
    }
}
