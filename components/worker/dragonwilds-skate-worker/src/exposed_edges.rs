//! Geometry-derived exposed grind segments. Units are Unreal centimetres.
//!
//! This authors host rails; it does not change Skate's classifier, contacts,
//! input, or candidate cap. No mesh names or positive gap tolerance are used.
//! A segment is a real boundary/convex crease of supplied collision triangles.
//! Swept probe envelopes remove blocked intervals continuously, not by voting
//! at a few sample points. The radius-0.1cm raised capsule is conservatively
//! enclosed by an oriented box, so corner-near contacts can remove extra rail.
use crate::compact_world::Bounds;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, VecDeque};

pub type Triangle = [[f64; 3]; 3];
type V = [f64; 3];
type Key = [i64; 3];
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Config {
    pub truck_distance_cm: f64,
    pub tolerance_cm: f64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            truck_distance_cm: 24.3,
            tolerance_cm: 1e-5,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Segment {
    pub a: V,
    pub b: V,
}
#[derive(Default, Debug, Serialize, Deserialize)]
pub struct Census {
    pub input_triangles: usize,
    pub degenerate_triangles: usize,
    pub closed_components_reoriented: usize,
    pub ambiguous_components: usize,
    pub unique_edges: usize,
    pub coplanar_edges: usize,
    pub concave_edges: usize,
    pub smooth_support_seams: usize,
    pub coplanar_overlap_spans: usize,
    pub supported_step_lifts: usize,
    pub collinear_overlap_unions: usize,
    pub proved_convex_solids: usize,
    pub buried_edge_spans: usize,
    pub underside_or_vertical_edges: usize,
    pub candidate_edges: usize,
    pub lower_crown_candidates: usize,
    pub blocked_candidates: usize,
    pub emitted_segments: usize,
}
#[derive(Debug)]
pub struct Patch {
    pub segments: Vec<Segment>,
    pub census: Census,
    pub dependencies: Vec<Bounds>,
}
fn add(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] + b[i])
}
fn sub(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] - b[i])
}
fn mul(a: V, b: f64) -> V {
    a.map(|x| x * b)
}
fn dot(a: V, b: V) -> f64 {
    (0..3).map(|i| a[i] * b[i]).sum()
}
fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn length(a: V) -> f64 {
    dot(a, a).sqrt()
}
fn unit(a: V) -> Option<V> {
    let n = length(a);
    (n.is_finite() && n > 1e-12).then(|| mul(a, 1. / n))
}
fn key(a: V, tolerance: f64) -> Key {
    a.map(|x| (x / tolerance).round() as i64)
}
fn triangle_key(tri: Triangle, tolerance: f64) -> [Key; 3] {
    let mut k = tri.map(|p| key(p, tolerance));
    k.sort();
    k
}
fn valid_bounds(b: Bounds) -> bool {
    (0..3).all(|i| b.min[i].is_finite() && b.max[i].is_finite() && b.min[i] < b.max[i])
}
fn lex(a: V, b: V) -> std::cmp::Ordering {
    (0..3)
        .map(|i| a[i].total_cmp(&b[i]))
        .find(|v| !v.is_eq())
        .unwrap_or(std::cmp::Ordering::Equal)
}
#[derive(Clone, Copy)]
struct Face {
    tri: Triangle,
    n: V,
    center: V,
}
#[derive(Clone, Copy)]
struct Use {
    face: usize,
    a: V,
    b: V,
    forward: bool,
}
type Edges = BTreeMap<(Key, Key), Vec<Use>>;

/// Make internally consistent closed components outward. Open/nonmanifold
/// patches keep their existing collision winding; no guessed volume is used.
fn orient(
    faces: &mut [Face],
    edges: &Edges,
    census: &mut Census,
    tolerance: f64,
    solids: &mut Vec<Vec<usize>>,
) -> Vec<usize> {
    let mut graph = vec![Vec::<(usize, bool)>::new(); faces.len()];
    let mut closed = vec![true; faces.len()];
    for uses in edges.values() {
        if uses.len() != 2 {
            for u in uses {
                closed[u.face] = false;
            }
        }
        for pair in uses.windows(2) {
            let a = pair[0];
            let b = pair[1];
            let flip = a.forward == b.forward;
            graph[a.face].push((b.face, flip));
            graph[b.face].push((a.face, flip));
        }
    }
    let mut sign = vec![None; faces.len()];
    let mut components = vec![0; faces.len()];
    for seed in 0..faces.len() {
        if sign[seed].is_some() {
            continue;
        }
        let mut queue = VecDeque::from([seed]);
        sign[seed] = Some(false);
        let mut members = vec![];
        let mut consistent = true;
        while let Some(i) = queue.pop_front() {
            components[i] = seed;
            members.push(i);
            let current = sign[i].unwrap();
            for &(j, invert) in &graph[i] {
                let wanted = current ^ invert;
                match sign[j] {
                    None => {
                        sign[j] = Some(wanted);
                        queue.push_back(j);
                    }
                    Some(v) => {
                        if v != wanted {
                            consistent = false;
                        }
                    }
                }
            }
        }
        if !consistent || !members.iter().all(|&i| closed[i]) {
            census.ambiguous_components += 1;
            continue;
        }
        let origin = faces[seed].tri[0];
        let volume = members
            .iter()
            .map(|&i| {
                let t = faces[i].tri;
                let v = dot(
                    sub(t[0], origin),
                    cross(sub(t[1], origin), sub(t[2], origin)),
                );
                if sign[i].unwrap() { -v } else { v }
            })
            .sum::<f64>();
        if !volume.is_finite() || volume.abs() <= tolerance.powi(3) {
            census.ambiguous_components += 1;
            continue;
        }
        let mut changed = false;
        for &i in &members {
            if sign[i].unwrap() ^ (volume < 0.) {
                faces[i].n = mul(faces[i].n, -1.);
                changed = true;
            }
        }
        if changed {
            census.closed_components_reoriented += 1;
        } else {
            // Raw outward ONE_SIDED closed shells can prove filled occupancy.
            // An inward shell may represent an accessible container; the
            // authoring-normal correction must never make it a filled solid.
            solids.push(members);
        }
    }
    components
}

struct Solid {
    bounds: Bounds,
    planes: Vec<(V, V)>,
}
struct SolidNode {
    bounds: Bounds,
    children: Option<(usize, usize)>,
    solids: Vec<usize>,
}
struct Solids {
    values: Vec<Solid>,
    nodes: Vec<SolidNode>,
}
fn overlaps(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.max[i] && b.min[i] <= a.max[i])
}
impl Solids {
    fn build(faces: &[Face], components: Vec<Vec<usize>>, tolerance: f64) -> Self {
        let mut values = vec![];
        for component in components {
            // This is an optional proof, not a geometry omission. Bound its
            // quadratic convexity check; unproved components remain untouched.
            if component.len() > 512 {
                continue;
            }
            let points: Vec<_> = component.iter().flat_map(|&i| faces[i].tri).collect();
            if component.iter().any(|&i| {
                points
                    .iter()
                    .any(|&p| dot(faces[i].n, sub(p, faces[i].tri[0])) > tolerance)
            }) {
                continue;
            }
            let bounds = Bounds {
                min: std::array::from_fn(|i| {
                    points.iter().map(|p| p[i]).fold(f64::INFINITY, f64::min)
                }),
                max: std::array::from_fn(|i| {
                    points
                        .iter()
                        .map(|p| p[i])
                        .fold(f64::NEG_INFINITY, f64::max)
                }),
            };
            values.push(Solid {
                bounds,
                planes: component
                    .into_iter()
                    .map(|i| (faces[i].n, faces[i].tri[0]))
                    .collect(),
            });
        }
        let mut result = Self {
            values,
            nodes: vec![],
        };
        if !result.values.is_empty() {
            result.node((0..result.values.len()).collect());
        }
        result
    }
    fn node(&mut self, mut ids: Vec<usize>) -> usize {
        let bounds = Bounds {
            min: std::array::from_fn(|i| {
                ids.iter()
                    .map(|&id| self.values[id].bounds.min[i])
                    .fold(f64::INFINITY, f64::min)
            }),
            max: std::array::from_fn(|i| {
                ids.iter()
                    .map(|&id| self.values[id].bounds.max[i])
                    .fold(f64::NEG_INFINITY, f64::max)
            }),
        };
        let index = self.nodes.len();
        self.nodes.push(SolidNode {
            bounds,
            children: None,
            solids: vec![],
        });
        if ids.len() <= 8 {
            self.nodes[index].solids = ids;
            return index;
        }
        let axis = (0..3)
            .max_by(|&a, &b| {
                (bounds.max[a] - bounds.min[a]).total_cmp(&(bounds.max[b] - bounds.min[b]))
            })
            .unwrap();
        ids.sort_by(|&a, &b| {
            let a = self.values[a].bounds;
            let b = self.values[b].bounds;
            (a.min[axis] + a.max[axis]).total_cmp(&(b.min[axis] + b.max[axis]))
        });
        let right = ids.split_off(ids.len() / 2);
        let left = self.node(ids);
        let right = self.node(right);
        self.nodes[index].children = Some((left, right));
        index
    }
    fn buried(&self, edge: Segment, up: V, tolerance: f64, padding: f64) -> Vec<Span> {
        if self.nodes.is_empty() {
            return vec![];
        }
        let a = add(edge.a, mul(up, tolerance * 4.));
        let d = sub(edge.b, edge.a);
        let len = length(d);
        let b = add(a, d);
        let bounds = Bounds {
            min: std::array::from_fn(|i| a[i].min(b[i])),
            max: std::array::from_fn(|i| a[i].max(b[i])),
        };
        let mut spans = vec![];
        self.visit(0, bounds, a, d, len, padding, &mut spans);
        union(spans)
    }
    fn visit(
        &self,
        id: usize,
        bounds: Bounds,
        a: V,
        d: V,
        len: f64,
        padding: f64,
        out: &mut Vec<Span>,
    ) {
        let node = &self.nodes[id];
        if !overlaps(node.bounds, bounds) {
            return;
        }
        if let Some((l, r)) = node.children {
            self.visit(l, bounds, a, d, len, padding, out);
            self.visit(r, bounds, a, d, len, padding, out);
            return;
        }
        for &index in &node.solids {
            let s = &self.values[index];
            if !overlaps(s.bounds, bounds) {
                continue;
            }
            let (mut lo, mut hi) = (0f64, 1f64);
            for &(n, p) in &s.planes {
                let start = dot(n, sub(a, p)) + padding;
                let delta = dot(n, d);
                if delta == 0. {
                    if start >= 0. {
                        hi = -1.;
                        break;
                    }
                } else {
                    let t = -start / delta;
                    if delta > 0. {
                        hi = hi.min(t);
                    } else {
                        lo = lo.max(t);
                    }
                }
                if hi <= lo {
                    break;
                }
            }
            if hi > lo && lo <= 1. && hi >= 0. {
                out.push((lo.max(0.) * len, hi.min(1.) * len));
            }
        }
    }
}

/// Closed halfspaces are used for clipping. Half-open ownership is resolved by
/// each nonzero span's midpoint, retaining every actual endpoint once joined.
fn owned(a: V, b: V, owner: Bounds, tolerance: f64) -> Option<Segment> {
    let d = sub(b, a);
    let (mut lo, mut hi) = (0f64, 1f64);
    for i in 0..3 {
        if d[i].abs() < 1e-14 {
            if a[i] < owner.min[i] || a[i] > owner.max[i] {
                return None;
            }
        } else {
            let mut near = (owner.min[i] - a[i]) / d[i];
            let mut far = (owner.max[i] - a[i]) / d[i];
            if near > far {
                std::mem::swap(&mut near, &mut far);
            }
            lo = lo.max(near);
            hi = hi.min(far);
        }
    }
    if hi <= lo {
        return None;
    }
    // Calculate both cuts from the original, canonically ordered endpoints.
    // Reconstructing the second from the first cut introduces a cell-dependent
    // ULP and breaks exact endpoint stitching at distant world coordinates.
    let at = |t: f64| {
        if t == 0. {
            return a;
        }
        if t == 1. {
            return b;
        }
        let mut p = add(a, mul(d, t));
        for i in 0..3 {
            if d[i] != 0. {
                for plane in [owner.min[i], owner.max[i]] {
                    if (plane - a[i]) / d[i] == t {
                        p[i] = plane;
                    }
                }
            }
        }
        p
    };
    let a = at(lo);
    let b = at(hi);
    let mid = mul(add(a, b), 0.5);
    if (0..3).any(|i| mid[i] < owner.min[i] || mid[i] >= owner.max[i])
        || length(sub(b, a)) <= tolerance
    {
        return None;
    }
    Some(Segment { a, b })
}
/// A triangle clipped by four halfspaces has at most seven vertices. Twelve
/// leaves spare capacity while removing millions of per-probe heap allocations.
/// Arithmetic, vertex order and inclusive boundary predicates are unchanged.
#[derive(Clone, Copy)]
struct Polygon {
    vertices: [V; 12],
    len: usize,
}
impl Polygon {
    fn new() -> Self {
        Self {
            vertices: [[0.; 3]; 12],
            len: 0,
        }
    }
    fn is_empty(&self) -> bool {
        self.len == 0
    }
    fn iter(&self) -> std::slice::Iter<'_, V> {
        self.vertices[..self.len].iter()
    }
    fn last(&self) -> Option<&V> {
        self.vertices[..self.len].last()
    }
    fn push(&mut self, p: V) {
        assert!(
            self.len < 12,
            "Convex clipped triangle exceeded its mathematical vertex bound"
        );
        self.vertices[self.len] = p;
        self.len += 1;
    }
}
impl FromIterator<V> for Polygon {
    fn from_iter<T: IntoIterator<Item = V>>(values: T) -> Self {
        let mut p = Self::new();
        for value in values {
            p.push(value);
        }
        p
    }
}
impl IntoIterator for Polygon {
    type Item = V;
    type IntoIter = std::iter::Take<std::array::IntoIter<V, 12>>;
    fn into_iter(self) -> Self::IntoIter {
        self.vertices.into_iter().take(self.len)
    }
}
fn clip(poly: Polygon, axis: usize, bound: f64, keep_less: bool) -> Polygon {
    if poly.is_empty() {
        return poly;
    }
    let mut out = Polygon::new();
    let mut prev = *poly.last().unwrap();
    let inside = |p: V| {
        if keep_less {
            p[axis] <= bound
        } else {
            p[axis] >= bound
        }
    };
    let mut before = inside(prev);
    for p in poly {
        let now = inside(p);
        if now != before {
            let fraction = (bound - prev[axis]) / (p[axis] - prev[axis]);
            let mut hit = add(prev, mul(sub(p, prev), fraction));
            hit[axis] = bound;
            out.push(hit);
        }
        if now {
            out.push(p);
        }
        prev = p;
        before = now;
    }
    out
}
type Span = (f64, f64);
fn union(mut spans: Vec<Span>) -> Vec<Span> {
    spans.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
    let mut out: Vec<Span> = vec![];
    for span in spans {
        if let Some(last) = out.last_mut() {
            if span.0 <= last.1 {
                last.1 = last.1.max(span.1);
                continue;
            }
        }
        out.push(span);
    }
    out
}
fn intersect(a: &[Span], b: &[Span]) -> Vec<Span> {
    let (mut i, mut j) = (0, 0);
    let mut out = vec![];
    while i < a.len() && j < b.len() {
        let lo = a[i].0.max(b[j].0);
        let hi = a[i].1.min(b[j].1);
        if lo <= hi {
            out.push((lo, hi));
        }
        if a[i].1 < b[j].1 {
            i += 1;
        } else {
            j += 1;
        }
    }
    out
}
/// Projection of triangle intersections with a moving probe's swept box.
/// Local X is rail distance, Y is ray extent, Z is normal to its sweep plane.
fn intervals(
    tris: &[Triangle],
    origin: V,
    direction: V,
    axis: V,
    half: f64,
    thickness: f64,
    len: f64,
    along_padding: f64,
) -> Vec<Span> {
    let normal = cross(direction, axis);
    let mut spans = vec![];
    for t in tris {
        let mut p: Polygon = t
            .iter()
            .map(|&p| {
                let delta = sub(p, origin);
                [dot(delta, direction), dot(delta, axis), dot(delta, normal)]
            })
            .collect();
        // An outward padded AABB of the already projected triangle encloses
        // every point produced by the unchanged convex polygon clipping below.
        // No near-boundary triangle is rejected by this optional fast path.
        let lo: V = std::array::from_fn(|i| p.iter().map(|v| v[i]).fold(f64::INFINITY, f64::min));
        let hi: V =
            std::array::from_fn(|i| p.iter().map(|v| v[i]).fold(f64::NEG_INFINITY, f64::max));
        let epsilon = lo
            .into_iter()
            .chain(hi)
            .chain([len, half, thickness, along_padding])
            .map(f64::abs)
            .fold(1., f64::max)
            * f64::EPSILON
            * 64.;
        if lo[2] > thickness + epsilon
            || hi[2] < -thickness - epsilon
            || lo[1] > half + epsilon
            || hi[1] < -half - epsilon
            || lo[0] > len + along_padding + epsilon
            || hi[0] < -along_padding - epsilon
        {
            continue;
        }
        for (axis, bound, less) in [
            (2, thickness, true),
            (2, -thickness, false),
            (1, half, true),
            (1, -half, false),
        ] {
            p = clip(p, axis, bound, less);
            if p.is_empty() {
                break;
            }
        }
        if p.is_empty() {
            continue;
        }
        let lo = p.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min) - along_padding;
        let hi = p.iter().map(|p| p[0]).fold(f64::NEG_INFINITY, f64::max) + along_padding;
        if lo <= len && hi >= 0. {
            spans.push((lo.max(0.), hi.min(len)));
        }
    }
    union(spans)
}
fn subtract(len: f64, blocked: &[Span], padding: f64) -> Vec<Span> {
    let mut start = 0.;
    let mut out = vec![];
    for &(lo, hi) in blocked {
        if lo > start {
            out.push((start, (lo - padding).max(start)));
        }
        start = start.max(hi + padding);
    }
    if start < len {
        out.push((start, len));
    }
    out
}
/// A narrow convex rounded rail has many longitudinal facet creases but one
/// upper crown. For native ThinRail spans only, reject lower creases where the
/// SAME connected surface rises above them inside the native near-probe width.
/// Disconnected adjacent rails are never used to suppress one another.
fn higher_crown(
    tris: &[Triangle],
    origin: V,
    direction: V,
    side: V,
    up: V,
    len: f64,
    tolerance: f64,
) -> Vec<Span> {
    let mut spans = vec![];
    for tri in tris {
        let mut polygon: Polygon = tri
            .iter()
            .map(|&p| {
                let d = sub(p, origin);
                [dot(d, direction), dot(d, side), dot(d, up)]
            })
            .collect();
        for (axis, bound, less) in [
            (1, 9., true),
            (1, -9., false),
            (2, 9., true),
            (2, tolerance, false),
        ] {
            polygon = clip(polygon, axis, bound, less);
            if polygon.is_empty() {
                break;
            }
        }
        if polygon.is_empty() {
            continue;
        }
        let lo = polygon
            .iter()
            .map(|p| p[0])
            .fold(f64::INFINITY, f64::min)
            .max(0.);
        let hi = polygon
            .iter()
            .map(|p| p[0])
            .fold(f64::NEG_INFINITY, f64::max)
            .min(len);
        if hi - lo > tolerance {
            spans.push((lo, hi));
        }
    }
    union(spans)
}
fn query_bounds(a: V, b: V, side: V, up: V, config: &Config, padding: f64) -> Bounds {
    let extent = std::array::from_fn::<_, 3, _>(|i| {
        side[i].abs() * config.truck_distance_cm.max(9.1)
            + up[i].abs() * (config.truck_distance_cm * 1.06).max(13.1)
            // The raised capsule also reaches beyond each longitudinal end.
            + 0.1
            + padding
    });
    Bounds {
        min: std::array::from_fn(|i| a[i].min(b[i]) - extent[i]),
        max: std::array::from_fn(|i| a[i].max(b[i]) + extent[i]),
    }
}

/// Highest actual upward support in the edge's vertical plane, within the
/// original four-centimetre center-ray window. Never clip a taller obstacle
/// into the admissible height range: only whole intersection polygons whose
/// maximum is inside the window can nominate a height.
fn supported_lift(
    edge: Segment,
    tris: &[Triangle],
    direction: V,
    side: V,
    up: V,
    config: &Config,
    padding: f64,
) -> Option<Segment> {
    let len = length(sub(edge.b, edge.a));
    let limit = 4. - 2. * padding;
    let tolerance = config.tolerance_cm;
    if limit <= tolerance {
        return None;
    }
    let supporting: Vec<_> = tris
        .iter()
        .copied()
        .filter(|t| {
            unit(cross(sub(t[1], t[0]), sub(t[2], t[0]))).is_some_and(|n| dot(n, up) > 1e-6)
        })
        .collect();
    let mut height = 0f64;
    for tri in &supporting {
        let mut polygon: Polygon = tri
            .iter()
            .map(|&p| {
                let d = sub(p, edge.a);
                [dot(d, direction), dot(d, side), dot(d, up)]
            })
            .collect();
        for (axis, bound, less) in [
            (0, 0., false),
            (0, len, true),
            (1, tolerance, true),
            (1, -tolerance, false),
        ] {
            polygon = clip(polygon, axis, bound, less);
            if polygon.is_empty() {
                break;
            }
        }
        let h = polygon
            .iter()
            .map(|p| p[2])
            .fold(f64::NEG_INFINITY, f64::max);
        if h > height && h <= limit {
            height = h;
        }
    }
    if height <= tolerance * 8. {
        return None;
    }
    let raised = Segment {
        a: add(edge.a, mul(up, height)),
        b: add(edge.b, mul(up, height)),
    };
    let supported = intervals(
        &supporting,
        raised.a,
        direction,
        up,
        4. - padding,
        tolerance,
        len,
        0.,
    );
    // No positive longitudinal gap tolerance, even if the gap is short.
    if supported.len() != 1 || supported[0].0 > 0. || supported[0].1 < len {
        return None;
    }
    let left = intervals(
        tris,
        add(raised.a, mul(side, -9.)),
        direction,
        up,
        4. + padding,
        padding,
        len,
        padding,
    );
    let right = intervals(
        tris,
        add(raised.a, mul(side, 9.)),
        direction,
        up,
        4. + padding,
        padding,
        len,
        padding,
    );
    if !intersect(&left, &right).is_empty() {
        return None;
    }
    let cross = intervals(
        tris,
        add(raised.a, mul(up, 9.)),
        direction,
        side,
        9.1 + padding,
        0.1 + padding,
        len,
        0.1 + padding,
    );
    if !cross.is_empty() {
        return None;
    }
    Some(raised)
}

/// Exact overlapping coverage is redundant. Group only parallel lines within
/// numerical topology tolerance; never join a positive longitudinal gap.
fn union_collinear(segments: Vec<Segment>, tolerance: f64, census: &mut Census) -> Vec<Segment> {
    let mut directions: BTreeMap<Key, Vec<Segment>> = BTreeMap::new();
    for s in segments {
        let d = unit(sub(s.b, s.a)).unwrap();
        directions
            .entry(d.map(|x| (x * 1e7).round() as i64))
            .or_default()
            .push(s);
    }
    let mut result = vec![];
    for list in directions.into_values() {
        let origin = list[0].a;
        let d = unit(sub(list[0].b, origin)).unwrap();
        let up = unit(sub([0., 0., 1.], mul(d, d[2]))).unwrap();
        let side = cross(up, d);
        let mut lines: BTreeMap<(i64, i64), Vec<(f64, f64, Segment)>> = BTreeMap::new();
        for s in list {
            let a = sub(s.a, origin);
            let b = sub(s.b, origin);
            let ya = dot(a, side);
            let yb = dot(b, side);
            let za = dot(a, up);
            let zb = dot(b, up);
            if (ya - yb).abs() > tolerance || (za - zb).abs() > tolerance {
                result.push(s);
                continue;
            }
            let k = (
                (ya / (tolerance * 8.)).round() as i64,
                (za / (tolerance * 8.)).round() as i64,
            );
            lines.entry(k).or_default().push((dot(a, d), dot(b, d), s));
        }
        for mut rows in lines.into_values() {
            rows.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
            let mut active = rows[0];
            for row in rows.into_iter().skip(1) {
                let offset = sub(row.2.a, active.2.a);
                let same_line = length(cross(offset, d)) <= tolerance;
                if same_line && row.0 <= active.1 {
                    if row.1 > active.1 {
                        active.1 = row.1;
                        active.2.b = row.2.b;
                    }
                    census.collinear_overlap_unions += 1;
                } else {
                    result.push(active.2);
                    active = row;
                }
            }
            result.push(active.2);
        }
    }
    result
}

/// `triangles` must contain complete input triangles intersecting the cell and
/// halo. `query` returns all physical triangles intersecting each reported box.
/// Omission, unsupported input, or excessive density is an error, never a cap.
pub fn derive(
    triangles: &[Triangle],
    owner: Bounds,
    query: &mut impl FnMut(Bounds) -> Vec<Triangle>,
    config: &Config,
) -> Result<Patch, String> {
    if !valid_bounds(owner)
        || !config.truck_distance_cm.is_finite()
        || config.truck_distance_cm <= 0.
        || config.truck_distance_cm > 100.
        || !config.tolerance_cm.is_finite()
        || !(1e-8..=0.01).contains(&config.tolerance_cm)
    {
        return Err("Invalid exposed-edge configuration".into());
    }
    if triangles.len() > 2_000_000 {
        return Err("Exposed-edge patch exceeds bounded triangle capacity".into());
    }
    let tolerance = config.tolerance_cm;
    let mut census = Census {
        input_triangles: triangles.len(),
        ..Default::default()
    };
    let mut faces = vec![];
    let mut edges: Edges = BTreeMap::new();
    for &tri in triangles {
        if !tri.iter().flatten().all(|x| x.is_finite() && x.abs() < 1e9) {
            return Err("Non-finite/out-of-range exposed-edge triangle".into());
        }
        let Some(n) = unit(cross(sub(tri[1], tri[0]), sub(tri[2], tri[0]))) else {
            census.degenerate_triangles += 1;
            continue;
        };
        let face = faces.len();
        faces.push(Face {
            tri,
            n,
            center: mul(add(add(tri[0], tri[1]), tri[2]), 1. / 3.),
        });
        for i in 0..3 {
            let (a, b) = (tri[i], tri[(i + 1) % 3]);
            let (ka, kb) = (key(a, tolerance), key(b, tolerance));
            if ka == kb {
                continue;
            }
            let forward = ka < kb;
            let index = if forward { (ka, kb) } else { (kb, ka) };
            edges.entry(index).or_default().push(Use {
                face,
                a,
                b,
                forward,
            });
        }
    }
    let mut solid_components = vec![];
    let components = orient(
        &mut faces,
        &edges,
        &mut census,
        tolerance,
        &mut solid_components,
    );
    let solids = Solids::build(&faces, solid_components, tolerance);
    census.proved_convex_solids = solids.values.len();
    let triangle_faces: HashMap<_, _> = faces
        .iter()
        .enumerate()
        .map(|(i, face)| (triangle_key(face.tri, tolerance), i))
        .collect();
    census.unique_edges = edges.len();
    let (mut segments, mut dependencies) = (vec![], vec![]);
    let halo = (config.truck_distance_cm * 2.1).max(100.);
    let selected = Bounds {
        min: owner.min.map(|x| x - halo),
        max: owner.max.map(|x| x + halo),
    };
    for uses in edges.values() {
        let first = uses[0];
        let (a, b) = if lex(first.a, first.b).is_gt() {
            (first.b, first.a)
        } else {
            (first.a, first.b)
        };
        if owned(a, b, selected, tolerance).is_none() {
            continue;
        }
        // Keep the complete physical edge for support, lift and dependency
        // decisions. Clipping before this point hides a taller support beyond
        // the cell boundary and makes neighboring cells choose different paths.
        let mut edge = Segment { a, b };
        let delta = sub(edge.b, edge.a);
        let len = length(delta);
        let direction = mul(delta, 1. / len);
        // Vertical wall corners are outside the requested geometry category.
        // Cooked boxes are often slightly warped; a five-degree numerical/scope
        // cone prevents an 89.8-degree wall corner becoming an apparent rail.
        if direction[2].abs() >= 0.9961946980917455 {
            census.underside_or_vertical_edges += 1;
            continue;
        }
        let Some(up) = unit(sub([0., 0., 1.], mul(direction, direction[2]))) else {
            census.underside_or_vertical_edges += 1;
            continue;
        };
        if !uses
            .iter()
            .any(|u| faces[u.face].n[2] > 0.08715574274765817)
        {
            census.underside_or_vertical_edges += 1;
            continue;
        }
        if uses.len() > 1 {
            let mut convex = false;
            let mut noncoplanar = false;
            for (i, x) in uses.iter().enumerate() {
                for y in &uses[i + 1..] {
                    let (f, g) = (faces[x.face], faces[y.face]);
                    if length(cross(f.n, g.n)) <= 1e-7 {
                        continue;
                    }
                    noncoplanar = true;
                    if dot(f.n, sub(g.center, a)) < -tolerance
                        || dot(g.n, sub(f.center, a)) < -tolerance
                    {
                        convex = true;
                    }
                }
            }
            if !noncoplanar {
                census.coplanar_edges += 1;
                continue;
            }
            if !convex {
                census.concave_edges += 1;
                continue;
            }
        }
        census.candidate_edges += 1;
        let side = cross(up, direction);
        // Enclose conversion to the actual f32 source rail coordinate system.
        let padding =
            a.into_iter().chain(b).map(f64::abs).fold(1., f64::max) * f64::from(f32::EPSILON) * 4.
                + tolerance;
        // A path deeper than the entire allowed four-centimetre lift remains
        // buried under every possible supported candidate. Its closed-solid
        // proof comes from the immutable input halo, so no world probe is needed.
        let deep = solids.buried(edge, up, tolerance, padding + 4.);
        if deep.len() == 1 && deep[0].0 <= 0. && deep[0].1 >= len {
            census.buried_edge_spans += 1;
            census.blocked_candidates += 1;
            continue;
        }
        let bounds = query_bounds(edge.a, edge.b, side, up, config, padding);
        dependencies.push(bounds);
        let tris = query(bounds);
        if tris.len() > 2_000_000
            || !tris
                .iter()
                .flatten()
                .flatten()
                .all(|x| x.is_finite() && x.abs() < 1e9)
        {
            return Err("Invalid/oversized exposed-edge query result".into());
        }
        let same_component: Vec<_> = tris
            .iter()
            .copied()
            .filter(|t| {
                triangle_faces
                    .get(&triangle_key(*t, tolerance))
                    .is_some_and(|i| components[*i] == components[first.face])
            })
            .collect();
        let curved_upper = same_component.iter().any(|t| {
            let n = faces[triangle_faces[&triangle_key(*t, tolerance)]].n;
            let vertical = dot(n, up);
            vertical > 0.08715574274765817 && (9. * dot(n, side) / vertical).abs() > 4. - padding
        });
        let lateral = same_component
            .iter()
            .flatten()
            .map(|p| dot(sub(*p, edge.a), side));
        let lateral_min = lateral.clone().fold(f64::INFINITY, f64::min);
        let lateral_max = lateral.fold(f64::NEG_INFINITY, f64::max);
        let curved_narrow_crown =
            curved_upper && lateral_max - lateral_min <= config.truck_distance_cm * 2. + tolerance;
        // A triangulated smooth upper surface is not an exposed crease. Both
        // face planes remain within the source's near-ray support band; losing
        // one hit at the outer end of a finite patch must not manufacture tiny
        // diagonal rails. A genuinely narrow connected rail keeps its crown.
        // A steep face elsewhere on a broad connected rock/terrain patch must
        // not grant the round-crown exception to a gentle interior diagonal.
        if !curved_narrow_crown
            && uses.len() > 1
            && uses.iter().all(|u| {
                let n = faces[u.face].n;
                let vertical = dot(n, up);
                vertical > 1e-6 && (9. * dot(n, side) / vertical).abs() <= 4. - padding
            })
        {
            census.smooth_support_seams += 1;
            continue;
        }
        if let Some(lifted) = supported_lift(edge, &tris, direction, side, up, config, padding) {
            edge = lifted;
            census.supported_step_lifts += 1;
        }
        let left = intervals(
            &tris,
            add(edge.a, mul(side, -9.)),
            direction,
            up,
            4. + padding,
            padding,
            len,
            padding,
        );
        let right = intervals(
            &tris,
            add(edge.a, mul(side, 9.)),
            direction,
            up,
            4. + padding,
            padding,
            len,
            padding,
        );
        let raised = intervals(
            &tris,
            add(edge.a, mul(up, 9.)),
            direction,
            side,
            9.1 + padding,
            0.1 + padding,
            len,
            0.1 + padding,
        );
        // A round crown wider than the nine-centimetre near offsets can still
        // have enough curvature for those rays to clear it (e.g. radius10cm).
        // Its local upper-face curvature distinguishes it from a warped flat
        // box top. The original truck span bounds the narrow-surface category.
        let narrow = lateral_max - lateral_min <= 18. + tolerance || curved_narrow_crown;
        // Determine narrowness from this connected surface, so a disconnected
        // nearby rail cannot turn its lower facets back into extra ledges.
        let own_left = intervals(
            &same_component,
            add(edge.a, mul(side, -9.)),
            direction,
            up,
            4. + padding,
            padding,
            len,
            padding,
        );
        let own_right = intervals(
            &same_component,
            add(edge.a, mul(side, 9.)),
            direction,
            up,
            4. + padding,
            padding,
            len,
            padding,
        );
        let thin = if narrow {
            vec![(0., len)]
        } else {
            subtract(
                len,
                &union(own_left.into_iter().chain(own_right).collect()),
                0.,
            )
        };
        let lower = if thin.is_empty() {
            vec![]
        } else {
            intersect(
                &thin,
                &higher_crown(&same_component, edge.a, direction, side, up, len, tolerance),
            )
        };
        if !lower.is_empty() {
            census.lower_crown_candidates += 1;
        }
        // Nonconforming triangle T-junctions do not share an edge-map key.
        // Detect coverage on both sides in the exact supporting face plane;
        // unlike the native nine-centimetre probes this also rejects seams in
        // narrow flat pieces. The offset is numerical tolerance, not gap glue.
        let mut coplanar = vec![];
        for u in uses {
            let n = faces[u.face].n;
            let vertical = dot(n, up);
            if vertical <= 1e-6 {
                continue;
            }
            let flat: Vec<_> = tris
                .iter()
                .copied()
                .filter(|t| t.iter().all(|p| dot(n, sub(*p, edge.a)).abs() <= tolerance))
                .collect();
            if flat.is_empty() {
                continue;
            }
            let offset = tolerance * 4.;
            let shift = sub(mul(side, offset), mul(up, offset * dot(n, side) / vertical));
            let a = intervals(
                &flat,
                sub(edge.a, shift),
                direction,
                up,
                tolerance,
                tolerance,
                len,
                0.,
            );
            let b = intervals(
                &flat,
                add(edge.a, shift),
                direction,
                up,
                tolerance,
                tolerance,
                len,
                0.,
            );
            coplanar.extend(
                intersect(&a, &b)
                    .into_iter()
                    .filter(|(lo, hi)| hi - lo > tolerance * 16.),
            );
        }
        census.coplanar_overlap_spans += coplanar.len();
        let buried = solids.buried(edge, up, tolerance, padding);
        census.buried_edge_spans += buried.len();
        let blocked = union(
            intersect(&left, &right)
                .into_iter()
                .chain(raised)
                .chain(lower)
                .chain(coplanar)
                .chain(buried)
                .collect(),
        );
        let spans = subtract(len, &blocked, padding);
        let mut emitted = false;
        for (start, end) in spans {
            if end - start <= tolerance {
                continue;
            }
            segments.push(Segment {
                a: if start == 0. {
                    edge.a
                } else {
                    add(edge.a, mul(direction, start))
                },
                b: if end == len {
                    edge.b
                } else {
                    add(edge.a, mul(direction, end))
                },
            });
            emitted = true;
        }
        if !emitted {
            census.blocked_candidates += 1;
        }
    }
    segments = union_collinear(segments, tolerance, &mut census)
        .into_iter()
        .filter_map(|s| owned(s.a, s.b, owner, tolerance))
        .collect();
    segments.sort_by(|a, b| lex(a.a, b.a).then(lex(a.b, b.b)));
    segments.dedup_by(|a, b| a.a == b.a && a.b == b.b);
    census.emitted_segments = segments.len();
    Ok(Patch {
        segments,
        census,
        dependencies,
    })
}

#[cfg(test)]
mod swept_bounds_tests {
    use super::*;
    fn bits(spans: Vec<(f64, f64)>) -> Vec<(u64, u64)> {
        spans
            .into_iter()
            .map(|(a, b)| (a.to_bits(), b.to_bits()))
            .collect()
    }
    // Prior exact interval implementation from source6037ec0e, kept as a regression
    // oracle for the conservative projected-triangle bounds fast path.
    fn reference(
        tris: &[Triangle],
        origin: V,
        direction: V,
        axis: V,
        half: f64,
        thickness: f64,
        len: f64,
        along_padding: f64,
    ) -> Vec<Span> {
        let normal = cross(direction, axis);
        let mut spans = vec![];
        for t in tris {
            let mut p: Polygon = t
                .iter()
                .map(|&p| {
                    let delta = sub(p, origin);
                    [dot(delta, direction), dot(delta, axis), dot(delta, normal)]
                })
                .collect();
            for (axis, bound, less) in [
                (2, thickness, true),
                (2, -thickness, false),
                (1, half, true),
                (1, -half, false),
            ] {
                p = clip(p, axis, bound, less);
                if p.is_empty() {
                    break;
                }
            }
            if p.is_empty() {
                continue;
            }
            let lo = p.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min) - along_padding;
            let hi = p.iter().map(|p| p[0]).fold(f64::NEG_INFINITY, f64::max) + along_padding;
            if lo <= len && hi >= 0. {
                spans.push((lo.max(0.), hi.min(len)));
            }
        }
        union(spans)
    }
    #[test]
    fn swept_bounds_match_reference_at_tangencies_and_distant_rotations() {
        let mut state = 91u64;
        let mut next = || {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((state >> 11) as f64) / (1u64 << 53) as f64
        };
        for i in 0..4096 {
            let yaw = next() * 6.28;
            let slope = next() * 1.2 - 0.6;
            let direction = [
                yaw.cos() * slope.cos(),
                yaw.sin() * slope.cos(),
                slope.sin(),
            ];
            let axis = [
                -yaw.cos() * slope.sin(),
                -yaw.sin() * slope.sin(),
                slope.cos(),
            ];
            let normal = [yaw.sin(), -yaw.cos(), 0.];
            let origin = if i % 2 == 0 {
                [730_123.23, -450_000.12, 234_124.4]
            } else {
                [0.; 3]
            };
            let half = 4.;
            let thickness = if i % 3 == 0 { 0.00001 } else { 0.1 };
            let len = 150.;
            let pad = 0.03;
            let mut tris = vec![];
            for k in 0..6 {
                let mut t = [[0.; 3]; 3];
                for p in &mut t {
                    let x = if k == 0 {
                        len + pad + ((i % 3) as f64 - 1.) * 1e-12
                    } else {
                        next() * 300. - 75.
                    };
                    let y = if k == 1 {
                        half + ((i % 3) as f64 - 1.) * 1e-12
                    } else {
                        next() * 20. - 10.
                    };
                    let z = if k == 2 {
                        -thickness + ((i % 3) as f64 - 1.) * 1e-12
                    } else {
                        next() * 2. - 1.
                    };
                    *p = std::array::from_fn(|a| {
                        origin[a] + direction[a] * x + axis[a] * y + normal[a] * z
                    });
                }
                tris.push(t);
            }
            assert_eq!(
                bits(reference(
                    &tris, origin, direction, axis, half, thickness, len, pad
                )),
                bits(intervals(
                    &tris, origin, direction, axis, half, thickness, len, pad
                )),
                "case{i}"
            );
        }
    }
}
