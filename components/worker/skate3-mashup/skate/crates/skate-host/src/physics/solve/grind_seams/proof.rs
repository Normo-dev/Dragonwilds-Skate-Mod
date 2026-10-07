//! Geometric proof for a small supported lip, independent of Skate's solver.
//! A missing triangle, positive support gap, tall face or rail end rejects it.
use skate_core::{math::Vector3, physics::board_world::WorldTriangle};
pub type V = [f64; 3];
pub fn add(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] + b[i])
}
pub fn sub(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] - b[i])
}
pub fn mul(a: V, s: f64) -> V {
    a.map(|v| v * s)
}
pub fn dot(a: V, b: V) -> f64 {
    a.iter().zip(b).map(|(a, b)| a * b).sum()
}
pub fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
pub fn unit(a: V) -> Option<V> {
    let n = dot(a, a).sqrt();
    (n.is_finite() && n > 1e-8).then(|| mul(a, 1. / n))
}
pub fn v(a: Vector3) -> V {
    [a.x as f64, a.y as f64, a.z as f64]
}
pub fn core(a: V) -> Vector3 {
    Vector3::new(a[0] as f32, a[1] as f32, a[2] as f32)
}
#[derive(Clone, Copy)]
pub struct Frame {
    pub start: V,
    pub end: V,
    pub direction: V,
    pub deck: V,
    pub truck: f64,
    pub width: f64,
}
pub struct Proof {
    pub direction: V,
    pub up: V,
    pub low: f64,
    pub high: f64,
}
/// A coplanar crown corner may originate from its top face or the exact
/// leading vertical face. A touching lateral/opposite face is insufficient.
pub fn coplanar_source(source: &WorldTriangle, origin: V, up: V, travel: V) -> bool {
    let normal = v(source.triangle.feature.normal);
    let vertical = dot(normal, up);
    if !(vertical >= 0.60 || (vertical.abs() <= 0.05 && dot(normal, travel) < -0.20)) {
        return false;
    }
    let mut high = f64::NEG_INFINITY;
    for point in source.triangle.vertices {
        let height = dot(sub(v(point), origin), up);
        if !height.is_finite() { return false; }
        high = high.max(height);
    }
    high.abs() <= 0.002
}
fn clip(poly: Vec<V>, axis: usize, bound: f64, less: bool) -> Vec<V> {
    let Some(&last) = poly.last() else {
        return vec![];
    };
    let mut prev = last;
    let inside = |p: V| {
        if less {
            p[axis] <= bound
        } else {
            p[axis] >= bound
        }
    };
    let mut before = inside(prev);
    let mut out = vec![];
    for p in poly {
        let now = inside(p);
        if now != before {
            let t = (bound - prev[axis]) / (p[axis] - prev[axis]);
            let mut at = add(prev, mul(sub(p, prev), t));
            at[axis] = bound;
            out.push(at);
        }
        if now {
            out.push(p);
        }
        prev = p;
        before = now;
    }
    out
}
fn contains(point: V, triangle: [V; 3], normal: V) -> bool {
    if dot(sub(point, triangle[0]), normal).abs() > 0.002 {
        return false;
    }
    (0..3).all(|i| {
        dot(
            cross(
                sub(triangle[(i + 1) % 3], triangle[i]),
                sub(point, triangle[i]),
            ),
            normal,
        ) >= -0.002
            * dot(
                sub(triangle[(i + 1) % 3], triangle[i]),
                sub(triangle[(i + 1) % 3], triangle[i]),
            )
            .sqrt()
    })
}
pub fn prove(frame: Frame, contact: V, normal: V, triangles: &[WorldTriangle]) -> Option<Proof> {
    prove_internal(frame, contact, normal, triangles, false)
}

/// A separate strictly coplanar corner proof. The host additionally requires
/// exact native source/collider association; ordinary floor policy is unchanged.
pub fn prove_coplanar_corner(frame: Frame, contact: V, normal: V, triangles: &[WorldTriangle]) -> Option<Proof> {
    let p=prove_internal(frame,contact,normal,triangles,true)?;
    if dot(normal,p.up)<=0.80 || p.low.abs()>0.002 || p.high.abs()>0.002 || p.high-p.low>0.002 {
        return None;
    }
    Some(p)
}

fn prove_internal(frame: Frame, contact: V, normal: V, triangles: &[WorldTriangle], coplanar: bool) -> Option<Proof> {
    let direction = unit(frame.direction)?;
    let rail = unit(sub(frame.end, frame.start))?;
    let up = unit(sub([0., 1., 0.], mul(rail, rail[1])))?;
    let side = cross(up, rail);
    // Retain floor contacts and lateral walls. Only a forward-blocking contact
    // on a currently admitted grind can reach the geometric proof.
    if dot(normal, direction) > -0.20 || (!coplanar && dot(normal, up) > 0.80) || dot(normal, up) < -0.05 {
        return None;
    }
    let delta = sub(contact, frame.start);
    let at = dot(delta, rail);
    let len = dot(sub(frame.end, frame.start), rail);
    if at < 0.06
        || at > len - 0.06
        || dot(sub(contact, frame.deck), rail).abs() > frame.truck + 0.40
        || dot(delta, side).abs() > frame.width + 0.04
    {
        return None;
    }
    let origin = add(frame.start, mul(rail, at));
    let height = dot(sub(contact, origin), up);
    if !(-0.10..=0.002).contains(&height) {
        return None;
    }
    // The support lane passes through the actual world contact, including
    // wheels hanging beside the crown. Its exact plane has no lateral gap glue.
    let origin = add(origin, mul(side, dot(sub(contact, origin), side)));
    let mut matched = false;
    for t in triangles {
        let n = v(t.triangle.feature.normal);
        let vertices = t.triangle.vertices.map(v);
        if dot(n, normal) > 0.25 && contains(contact, vertices, n) {
            if vertices.iter().any(|p| dot(sub(*p, origin), up) > 0.002) {
                return None;
            }
            matched = true;
        }
    }
    if !matched {
        return None;
    }
    let mut spans = vec![];
    let mut behind = f64::NEG_INFINITY;
    let mut ahead = f64::NEG_INFINITY;
    let (mut low, mut high) = (f64::INFINITY, f64::NEG_INFINITY);
    for t in triangles {
        if dot(v(t.triangle.feature.normal), up) < 0.60 {
            continue;
        }
        let mut poly: Vec<V> = t
            .triangle
            .vertices
            .iter()
            .map(|&p| {
                let d = sub(v(p), origin);
                [dot(d, rail), dot(d, up), dot(d, side)]
            })
            .collect();
        for (axis, bound, less) in [
            (2, 0., true),
            (2, 0., false),
            (0, -0.06, false),
            (0, 0.06, true),
            // A coplanar corner requires actual crown support throughout the
            // lane. Buried lower lintels cannot authorize or invalidate it.
            (1, if coplanar { -0.002 } else { -0.04 }, false),
            (1, 0.002, true),
        ] {
            poly = clip(poly, axis, bound, less);
            if poly.is_empty() {
                break;
            }
        }
        if poly.is_empty() {
            continue;
        }
        let lo = poly.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min);
        let hi = poly.iter().map(|p| p[0]).fold(f64::NEG_INFINITY, f64::max);
        if hi <= lo {
            continue;
        }
        spans.push((lo, hi));
        for p in &poly {
            low = low.min(p[1]);
            high = high.max(p[1]);
        }
        for (station, target) in [(-0.045, &mut behind), (0.045, &mut ahead)] {
            for i in 0..poly.len() {
                let a = poly[i];
                let b = poly[(i + 1) % poly.len()];
                if a[0].min(b[0]) <= station && station <= a[0].max(b[0]) {
                    let y = if a[0] == b[0] {
                        a[1].max(b[1])
                    } else {
                        a[1] + (b[1] - a[1]) * (station - a[0]) / (b[0] - a[0])
                    };
                    *target = target.max(y);
                }
            }
        }
    }
    spans.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut covered = -0.06;
    // Quantized, sloped source planes can differ by nanometers at their
    // shared seam. Allow 0.1 micrometer; never join a measurable gap.
    for (lo, hi) in spans {
        if lo > covered + 1e-7 {
            return None;
        }
        covered = covered.max(hi);
    }
    if covered < 0.06 - 1e-7
        || !behind.is_finite()
        || !ahead.is_finite()
        || high - low > 0.04
        || high.abs() > 0.002
    {
        return None;
    }
    Some(Proof {
        direction,
        up,
        low,
        high,
    })
}
