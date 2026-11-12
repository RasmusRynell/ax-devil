//! Python's arrival insertion, without quadratic scans or shifts on mixed-clock input.

use crate::Observation;

pub(crate) fn order(observations: &mut Vec<Observation>) {
    if observations.len() < 2 {
        return;
    }
    // Only a uniform comparison clock permits a stable sort. A shared frame alone is insufficient:
    // dual-clock arrivals still compare timestamps, which need not agree with their frames.
    if observations.iter().all(|o| o.time.timestamp().is_some()) {
        if !observations.is_sorted_by_key(|o| o.time.timestamp()) {
            observations.sort_by_key(|o| o.time.timestamp());
        }
    } else if observations.iter().all(|o| o.time.timestamp().is_none()) {
        if !observations.is_sorted_by_key(|o| o.time.frame()) {
            observations.sort_by_key(|o| o.time.frame());
        }
    } else {
        let mut root: Option<Box<Node>> = None;
        for observation in observations.drain(..) {
            let node = Box::new(Node::new(observation));
            let clock = usize::from(node.times[1].is_some());
            let at = root
                .as_ref()
                .map_or(0, |root| root.after_stop(clock, node.times[clock]));
            root = Some(Node::insert(root, node, at));
        }
        if let Some(root) = root {
            root.emit(observations);
        }
    }
}

// An AVL sequence tree. Each subtree records its minimum on both clocks, with a missing clock below
// every supplied value. It can therefore skip exactly the suffix Python would walk over, stopping at
// the rightmost incomparable or <= neighbour. Search and insertion cost O(log n); the whole ordering
// costs O(n log n) time and O(n) space, even when adversarial arrivals repeatedly cross a long suffix.
struct Node {
    observation: Observation,
    times: [Option<i64>; 2],
    minimum: [Option<i64>; 2],
    children: [Option<Box<Self>>; 2],
    height: usize,
    count: usize,
}

impl Node {
    fn new(observation: Observation) -> Self {
        let times = [
            observation.time.frame(),
            observation.time.timestamp().map(|t| t.0),
        ];
        Self {
            observation,
            times,
            minimum: times,
            children: [None, None],
            height: 1,
            count: 1,
        }
    }

    fn height(child: &Option<Box<Self>>) -> usize {
        child.as_ref().map_or(0, |node| node.height)
    }

    fn count(child: &Option<Box<Self>>) -> usize {
        child.as_ref().map_or(0, |node| node.count)
    }

    fn refresh(&mut self) {
        self.height = 1 + Self::height(&self.children[0]).max(Self::height(&self.children[1]));
        self.count = 1 + Self::count(&self.children[0]) + Self::count(&self.children[1]);
        self.minimum = self.times;
        for child in self.children.iter().flatten() {
            for clock in 0..2 {
                self.minimum[clock] = self.minimum[clock].min(child.minimum[clock]);
            }
        }
    }

    fn after_stop(&self, clock: usize, value: Option<i64>) -> usize {
        let left = Self::count(&self.children[0]);
        if let Some(right) = &self.children[1]
            && right.minimum[clock] <= value
        {
            return left + 1 + right.after_stop(clock, value);
        }
        if self.times[clock] <= value {
            return left + 1;
        }
        self.children[0]
            .as_ref()
            .map_or(0, |left| left.after_stop(clock, value))
    }

    fn insert(root: Option<Box<Self>>, node: Box<Self>, at: usize) -> Box<Self> {
        let Some(mut root) = root else {
            return node;
        };
        let left = Self::count(&root.children[0]);
        let side = usize::from(at > left);
        let offset = if side == 0 { at } else { at - left - 1 };
        root.children[side] = Some(Self::insert(root.children[side].take(), node, offset));
        root.refresh();
        let heights = root.children.each_ref().map(Self::height);
        let side = usize::from(heights[1] > heights[0]);
        if heights[side] > heights[1 - side] + 1 {
            // A height difference of two proves the heavy child exists.
            let child = root.children[side].as_ref().expect("heavy child");
            if Self::height(&child.children[1 - side]) > Self::height(&child.children[side]) {
                // The same height invariant proves the child exists after the borrow ends.
                let child = root.children[side].take().expect("heavy child");
                root.children[side] = Some(Self::rotate(child, 1 - side));
            }
            root = Self::rotate(root, side);
        }
        root
    }

    fn rotate(mut root: Box<Self>, side: usize) -> Box<Self> {
        // Called only toward a child whose height proves it exists.
        let mut promoted = root.children[side].take().expect("rotation child");
        root.children[side] = promoted.children[1 - side].take();
        root.refresh();
        promoted.children[1 - side] = Some(root);
        promoted.refresh();
        promoted
    }

    fn emit(self, out: &mut Vec<Observation>) {
        let [left, right] = self.children;
        if let Some(left) = left {
            left.emit(out);
        }
        out.push(self.observation);
        if let Some(right) = right {
            right.emit(out);
        }
    }
}
