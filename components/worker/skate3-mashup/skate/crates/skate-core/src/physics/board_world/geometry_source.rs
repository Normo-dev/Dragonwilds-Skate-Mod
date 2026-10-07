//! Host geometry storage/query interface. Narrow-phase and contact retention
//! remain owned by BoardWorld; providers retain stable canonical triangle IDs.
use super::{WorldTriangle, query_metadata::{Bounds, QueryMetadata}};
use crate::physics::contact::RetailContactMaterial;
use std::ops::Range;

pub trait WorldGeometry: Send + Sync {
    fn validate(&self) -> Result<(), &'static str>;
    fn triangle_count(&self) -> usize;
    fn metadata(&self) -> &QueryMetadata;
    /// None is an explicitly degenerate source primitive, never unavailable
    /// streamed data. All immutable source geometry must be available at load.
    fn triangle(&self, index: usize, material: RetailContactMaterial) -> Option<WorldTriangle>;
    fn triangle_bounds(&self, index: usize) -> Bounds;
    fn packed_surface(&self, index: usize) -> u16;
    /// Sorted, nonoverlapping ranges in canonical source order; conservative
    /// culling only. Invalid/absent query bounds mean every source primitive.
    fn candidate_ranges(&self, bounds: Option<Bounds>) -> Vec<Range<usize>>;
    fn candidate_mesh_indices(&self, bounds: Option<Bounds>) -> Vec<usize>;
    fn maximum_fatness(&self) -> f32;
    fn maximum_triangle_margin(&self) -> f32;
}
