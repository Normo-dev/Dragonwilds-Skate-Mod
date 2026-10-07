//! Validate a native support witness against its actual same-body collider.
use super::proof::{V, dot, sub};

pub fn extent(point: V, center: V, radius: f64, deck: V, side: V) -> Option<f64> {
    if !radius.is_finite()
        || radius < 0.
        || point
            .into_iter()
            .chain(center)
            .chain(deck)
            .chain(side)
            .any(|v| !v.is_finite())
    {
        return None;
    }
    // Match the existing locality padding, including authored collider fatness.
    let delta = sub(point, center);
    if dot(delta, delta) > (radius + 0.04).powi(2) {
        return None;
    }
    Some(dot(sub(point, deck), side).abs())
}
