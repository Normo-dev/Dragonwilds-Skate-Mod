//! Exact local reuse of already materialized CompactWorld query triangles.
//! Only boxes fully inside the source query region use this index; all other
//! boxes retain the original world callback. Result order is the input's
//! canonical source-triangle order, including overlapping duplicate faces.
use crate::compact_world::Bounds;
pub type Triangle = [[f64; 3]; 3];
struct Node {
    bound: Bounds,
    children: Option<(usize, usize)>,
    start: usize,
    end: usize,
}
pub struct LocalQuery<'a> {
    triangles: &'a [Triangle],
    region: Bounds,
    bounds: Vec<Bounds>,
    order: Vec<usize>,
    nodes: Vec<Node>,
}
fn overlap(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.max[i] && a.max[i] >= b.min[i])
}
impl<'a> LocalQuery<'a> {
    pub fn new(triangles: &'a [Triangle], region: Bounds) -> Self {
        let bounds: Vec<_> = triangles
            .iter()
            .map(|t| Bounds {
                min: std::array::from_fn(|i| t.iter().map(|p| p[i]).fold(f64::INFINITY, f64::min)),
                max: std::array::from_fn(|i| {
                    t.iter().map(|p| p[i]).fold(f64::NEG_INFINITY, f64::max)
                }),
            })
            .collect();
        let mut value = Self {
            triangles,
            region,
            bounds,
            order: (0..triangles.len()).collect(),
            nodes: vec![],
        };
        if !triangles.is_empty() {
            value.build(0, triangles.len());
        }
        value
    }
    fn build(&mut self, start: usize, end: usize) -> usize {
        let mut bound = self.bounds[self.order[start]];
        for &index in &self.order[start + 1..end] {
            let b = self.bounds[index];
            for i in 0..3 {
                bound.min[i] = bound.min[i].min(b.min[i]);
                bound.max[i] = bound.max[i].max(b.max[i]);
            }
        }
        let node = self.nodes.len();
        self.nodes.push(Node {
            bound,
            children: None,
            start,
            end,
        });
        if end - start > 16 {
            let axis = (0..3)
                .max_by(|&a, &b| {
                    (bound.max[a] - bound.min[a]).total_cmp(&(bound.max[b] - bound.min[b]))
                })
                .unwrap();
            let mid = (start + end) / 2;
            let bounds = &self.bounds;
            self.order[start..end].select_nth_unstable_by(mid - start, |&a, &b| {
                (bounds[a].min[axis] + bounds[a].max[axis])
                    .total_cmp(&(bounds[b].min[axis] + bounds[b].max[axis]))
                    .then(a.cmp(&b))
            });
            let left = self.build(start, mid);
            let right = self.build(mid, end);
            self.nodes[node].children = Some((left, right));
        }
        node
    }
    pub fn query(&self, b: Bounds) -> Option<Vec<Triangle>> {
        if !(0..3).all(|i| {
            b.min[i].is_finite()
                && b.max[i].is_finite()
                && b.min[i] <= b.max[i]
                && b.min[i] >= self.region.min[i]
                && b.max[i] <= self.region.max[i]
        }) {
            return None;
        }
        if self.nodes.is_empty() {
            return Some(vec![]);
        }
        let mut found = vec![];
        let mut stack = vec![0];
        while let Some(i) = stack.pop() {
            let n = &self.nodes[i];
            if !overlap(n.bound, b) {
                continue;
            }
            if let Some((a, c)) = n.children {
                stack.push(c);
                stack.push(a);
            } else {
                for &id in &self.order[n.start..n.end] {
                    if overlap(self.bounds[id], b) {
                        found.push(id);
                    }
                }
            }
        }
        found.sort_unstable();
        Some(found.into_iter().map(|i| self.triangles[i]).collect())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_exact_aabb_results_and_uncontained_fallback() {
        let region = Bounds {
            min: [-200.; 3],
            max: [200.; 3],
        };
        let mut tris = Vec::new();
        for i in 0..300 {
            let x = (i * 47 % 397) as f64 - 198.;
            let y = (i * 71 % 397) as f64 - 198.;
            tris.push([[x, y, -100.], [x + 13., y, 100.], [x, y + 7., 0.]]);
        }
        tris.push(tris[0]);
        let local = LocalQuery::new(&tris, region);
        for i in 0..100 {
            let x = (i * 23 % 300) as f64 - 150.;
            let y = (i * 19 % 300) as f64 - 150.;
            let q = Bounds {
                min: [x, y, -30.],
                max: [x + 25., y + 30., 30.],
            };
            let expected: Vec<_> = tris
                .iter()
                .copied()
                .filter(|t| {
                    (0..3).all(|a| {
                        t.iter().any(|p| p[a] <= q.max[a]) && t.iter().any(|p| p[a] >= q.min[a])
                    })
                })
                .collect();
            assert_eq!(local.query(q).unwrap(), expected);
        }
        assert!(
            local
                .query(Bounds {
                    min: [-201.; 3],
                    max: [1.; 3]
                })
                .is_none()
        );
        assert_eq!(LocalQuery::new(&[], region).query(region).unwrap().len(), 0);
    }
}
