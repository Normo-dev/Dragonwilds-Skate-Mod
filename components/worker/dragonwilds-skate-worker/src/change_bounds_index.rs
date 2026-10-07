//! Exact inclusive overlap lookup for one resident scene's changed bounds.
//! Node unions only reject candidates; an original input box proves every hit.
use crate::compact_world::Bounds;
use std::ops::Range;

const LINEAR_LIMIT: usize = 32;
const LEAF_SIZE: usize = 8;

fn valid(b: Bounds) -> bool {
    (0..3).all(|i| b.min[i].is_finite() && b.max[i].is_finite() && b.min[i] <= b.max[i])
}

fn overlap(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.max[i] && b.min[i] <= a.max[i])
}

struct Node {
    bounds: Bounds,
    children: Option<[usize; 2]>,
    range: Range<usize>,
}

pub struct ChangeBoundsIndex<'a> {
    bounds: &'a [Bounds],
    order: Vec<usize>,
    nodes: Vec<Node>,
}

impl<'a> ChangeBoundsIndex<'a> {
    pub fn new(bounds: &'a [Bounds]) -> Result<Self, String> {
        if bounds.iter().any(|&b| !valid(b)) {
            return Err("Invalid resident scene difference bounds".into());
        }
        let mut result = Self {
            bounds,
            order: Vec::new(),
            nodes: Vec::new(),
        };
        if bounds.len() > LINEAR_LIMIT {
            result.order = (0..bounds.len()).collect();
            result.nodes.reserve(bounds.len().div_ceil(LEAF_SIZE) * 4);
            result.build(0..bounds.len());
        }
        Ok(result)
    }

    fn build(&mut self, range: Range<usize>) -> usize {
        let bounds = self.order[range.clone()]
            .iter()
            .map(|&i| self.bounds[i])
            .reduce(|a, b| Bounds {
                min: std::array::from_fn(|i| a.min[i].min(b.min[i])),
                max: std::array::from_fn(|i| a.max[i].max(b.max[i])),
            })
            .unwrap();
        let index = self.nodes.len();
        self.nodes.push(Node {
            bounds,
            children: None,
            range: range.clone(),
        });
        if range.len() > LEAF_SIZE {
            let axis = (0..3)
                .max_by(|&a, &b| {
                    (bounds.max[a] - bounds.min[a]).total_cmp(&(bounds.max[b] - bounds.min[b]))
                })
                .unwrap();
            // Halving before addition keeps finite centers even when the full
            // extent or the unscaled endpoint sum overflows at extreme inputs.
            let center = |i: usize| self.bounds[i].min[axis] * 0.5 + self.bounds[i].max[axis] * 0.5;
            let middle = range.start + range.len() / 2;
            self.order[range.clone()].select_nth_unstable_by(range.len() / 2, |&a, &b| {
                center(a).total_cmp(&center(b)).then(a.cmp(&b))
            });
            let a = self.build(range.start..middle);
            let b = self.build(middle..range.end);
            self.nodes[index].children = Some([a, b]);
        }
        index
    }

    pub fn any_overlap(&self, query: Bounds) -> bool {
        fn visit(index: usize, tree: &ChangeBoundsIndex<'_>, query: Bounds) -> bool {
            let node = &tree.nodes[index];
            if !overlap(node.bounds, query) {
                return false;
            }
            if let Some([a, b]) = node.children {
                visit(a, tree, query) || visit(b, tree, query)
            } else {
                tree.order[node.range.clone()]
                    .iter()
                    .any(|&i| overlap(tree.bounds[i], query))
            }
        }
        if self.nodes.is_empty() {
            self.bounds.iter().any(|&b| overlap(b, query))
        } else {
            visit(0, self, query)
        }
    }
}
