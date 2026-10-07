//! Bounded memoization of an immutable imported geometry source. Cached values
//! are the original source's complete results; no geometry is reconstructed.
use skate_core::physics::{
    board_world::{WorldTriangle, geometry_source::WorldGeometry,
        query_metadata::{Bounds, QueryMetadata}},
    contact::RetailContactMaterial,
};
use std::{collections::{HashMap, VecDeque}, ops::Range, sync::{Arc, Mutex, OnceLock}};

const CAPACITY: usize = 65_536;

#[derive(Clone, Copy)]
struct Triangle {
    material: [u32; 3],
    value: Option<WorldTriangle>,
}
#[derive(Clone, Copy, Default)]
struct Entry { triangle: Option<Triangle>, bounds: Option<Bounds> }
struct Cache { entries: HashMap<usize, Entry>, fifo: VecDeque<usize>, capacity: usize }
impl Cache {
    fn entry(&mut self, id: usize) -> &mut Entry {
        if !self.entries.contains_key(&id) {
            while self.entries.len() >= self.capacity {
                let old = self.fifo.pop_front().expect("Geometry memo FIFO is complete");
                self.entries.remove(&old);
            }
            self.fifo.push_back(id);
            self.entries.insert(id, Entry::default());
        }
        self.entries.get_mut(&id).expect("Inserted geometry memo entry")
    }
}

struct Geometry { source: Arc<dyn WorldGeometry>, cache: Mutex<Cache> }
impl Geometry {
    fn new(source: Arc<dyn WorldGeometry>, capacity: usize) -> Self {
        assert!(capacity > 0);
        Self { source, cache: Mutex::new(Cache { entries: HashMap::new(), fifo: VecDeque::new(), capacity }) }
    }
}

/// Each installed source gets its own memo, including a source that revisits
/// previous geometry. The private profiling override permits identical-source
/// comparisons without rebuilding extraction caches or changing simulation.
pub(super) fn memo(source: Arc<dyn WorldGeometry>) -> Arc<dyn WorldGeometry> {
    static DISABLED: OnceLock<bool> = OnceLock::new();
    if *DISABLED.get_or_init(|| std::env::var_os("SKATE3_DISABLE_GEOMETRY_MEMO").is_some()) {
        return source;
    }
    Arc::new(Geometry::new(source, CAPACITY))
}

fn material_bits(material: RetailContactMaterial) -> [u32; 3] {
    [material.static_friction, material.dynamic_friction, material.restitution].map(f32::to_bits)
}
impl WorldGeometry for Geometry {
    fn validate(&self) -> Result<(), &'static str> { self.source.validate() }
    fn triangle_count(&self) -> usize { self.source.triangle_count() }
    fn metadata(&self) -> &QueryMetadata { self.source.metadata() }
    fn triangle(&self, index: usize, material: RetailContactMaterial) -> Option<WorldTriangle> {
        let bits = material_bits(material);
        if let Some(triangle) = self.cache.lock().unwrap().entries.get(&index)
            .and_then(|entry| entry.triangle).filter(|triangle| triangle.material == bits) {
            return triangle.value;
        }
        let value = self.source.triangle(index, material);
        self.cache.lock().unwrap().entry(index).triangle = Some(Triangle { material: bits, value });
        value
    }
    fn triangle_bounds(&self, index: usize) -> Bounds {
        if let Some(bounds) = self.cache.lock().unwrap().entries.get(&index).and_then(|entry| entry.bounds) {
            return bounds;
        }
        let bounds = self.source.triangle_bounds(index);
        self.cache.lock().unwrap().entry(index).bounds = Some(bounds);
        bounds
    }
    fn packed_surface(&self, index: usize) -> u16 { self.source.packed_surface(index) }
    fn candidate_ranges(&self, bounds: Option<Bounds>) -> Vec<Range<usize>> { self.source.candidate_ranges(bounds) }
    fn candidate_mesh_indices(&self, bounds: Option<Bounds>) -> Vec<usize> { self.source.candidate_mesh_indices(bounds) }
    fn maximum_fatness(&self) -> f32 { self.source.maximum_fatness() }
    fn maximum_triangle_margin(&self) -> f32 { self.source.maximum_triangle_margin() }
}

#[cfg(test)]
#[path = "cached_geometry_tests.rs"]
mod tests;
