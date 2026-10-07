//! Associate a displaced native prism witness with its actual source face.
//! No distance allowance substitutes for reproducing the original leaf.
use super::proof::{V, dot, mul, sub, v};
use skate_core::physics::{
    board_step::{BoardCollision, CollisionBody},
    board_world::{BoardWorldVolume, WorldTriangle},
    collision::WorldContactSettings,
    world_contact::{PrimitiveContactManifold, primitive_triangle_world_contacts},
};

/// One immutable solve frame only. All triangle leaf input bits and the
/// current collider ordinal identify a replay; query/velocities stay fixed.
pub const MAX_REPLAYS: usize = 32;
pub struct LeafCache {
    entries: Vec<(([u32;29],usize),Option<PrimitiveContactManifold>)>,
}
impl LeafCache {
    pub fn new() -> Self { Self{entries:Vec::with_capacity(MAX_REPLAYS)} }
    pub fn replay_count(&self) -> usize { self.entries.len() }
    pub fn replay(&mut self, source:&WorldTriangle, volume:&BoardWorldVolume,
        ordinal:usize, query:WorldContactSettings)->Option<PrimitiveContactManifold> {
        let t=&source.triangle;
        let lanes=|p:skate_core::math::Vector3|[p.x,p.y,p.z].map(f32::to_bits);
        let values=t.vertices.iter().flat_map(|p|lanes(*p))
            .chain(lanes(t.feature.normal))
            .chain(t.feature.edges.iter().flat_map(|p|lanes(*p)))
            .chain([t.feature.flags])
            .chain(t.feature.edge_cosines.map(f32::to_bits))
            .chain(t.edge_lengths.map(f32::to_bits))
            .chain([t.fatness.to_bits()]);
        let mut bits=[0;29];for(slot,value)in bits.iter_mut().zip(values){*slot=value;}
        let key=(bits,ordinal);
        if let Some((_,result))=self.entries.iter().find(|(k,_)|*k==key) { return *result; }
        if self.entries.len()==MAX_REPLAYS { return None; }
        let result=primitive_triangle_world_contacts(volume.primitive,*t,volume.linear_velocity,query);
        self.entries.push((key,result));
        result
    }
    pub fn recovered_point(&mut self, row:&BoardCollision, volume:&BoardWorldVolume,
        ordinal:usize, query:WorldContactSettings, source:&WorldTriangle)->Option<V> {
        if !eligible(row,volume,source) { return None; }
        let projected=projected_on_face(v(row.contact.position_on_b),source)?;
        let manifold=self.replay(source,volume,ordinal,query)?;
        matching(row,&manifold).then_some(projected)
    }
}

fn eligible(row:&BoardCollision, volume:&BoardWorldVolume, source:&WorldTriangle)->bool {
    matches!(row.body_a,CollisionBody::Board(_)) && row.body_b==CollisionBody::StaticWorld
        && volume.body==row.body_a && source.triangle.fatness==0.
}
fn matching(row:&BoardCollision, manifold:&PrimitiveContactManifold)->bool {
    manifold.normal==row.contact.normal && manifold.points[..manifold.count].iter().any(|pair|
        pair.a==row.contact.position_on_a && pair.b==row.contact.position_on_b)
}

#[allow(dead_code)] // Uncached reference used by the focused public proof tests.
pub fn recovered_point(
    row: &BoardCollision,
    volume: &BoardWorldVolume,
    query: WorldContactSettings,
    source: &WorldTriangle,
) -> Option<V> {
    if !eligible(row,volume,source) {
        return None;
    }
    let projected = projected_on_face(v(row.contact.position_on_b), source)?;
    let manifold = primitive_triangle_world_contacts(
        volume.primitive, source.triangle, volume.linear_velocity, query,
    )?;
    if !matching(row,&manifold) {
        return None;
    }
    Some(projected)
}

pub fn projected_on_face(point: V, source: &WorldTriangle) -> Option<V> {
    let vertices = source.triangle.vertices.map(v);
    let normal = v(source.triangle.feature.normal);
    let squared = dot(normal, normal);
    if !squared.is_finite() || squared < 1e-8 {
        return None;
    }
    let projected = sub(point, mul(normal, dot(sub(point, vertices[0]), normal) / squared));
    if projected.iter().any(|p| !p.is_finite()) {
        return None;
    }
    // The projected point must lie in this actual triangle, including only
    // the same sub-micrometer plane-intersection roundoff as support coverage.
    for i in 0..3 {
        let edge = sub(vertices[(i + 1) % 3], vertices[i]);
        if dot(super::proof::cross(edge, sub(projected, vertices[i])), normal)
            < -1e-7 * dot(edge, edge).sqrt() * squared.sqrt()
        {
            return None;
        }
    }
    Some(projected)
}
