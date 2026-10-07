#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
use compact_geometry::CompactGeometry;
use compact_world::CompactWorld;
use std::{path::PathBuf,sync::Arc};
use skate_core::{math::Vector3,physics::{board_world::{BoardWorld,WorldTriangle,geometry_source::WorldGeometry,BoardWorldVolume,ContactRetentionSettings},
    contact::RetailContactMaterial,world_contact::ContactPrimitive,collision::{Sphere,WorldContactSettings},board_step::{BoardCollision,CollisionBody}}};

fn material()->RetailContactMaterial {RetailContactMaterial{static_friction:0.7,dynamic_friction:0.4,restitution:0.2}}
fn fixture()->Arc<CompactGeometry> {
    let path=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture/export/manifest.json");
    Arc::new(CompactGeometry::new(Arc::new(CompactWorld::load(&path).unwrap()),[13.25,-17.125,4.75]).unwrap())
}
fn bits(triangle:WorldTriangle)->Vec<u32> {
    let t=triangle.triangle;let mut bits=vec![t.feature.flags,triangle.tag];
    for p in t.vertices.into_iter().chain([t.feature.normal]).chain(t.feature.edges){bits.extend([p.x,p.y,p.z].map(f32::to_bits));}
    bits.extend(t.edge_lengths.map(f32::to_bits));bits.extend(t.feature.edge_cosines.map(f32::to_bits));bits.push(t.fatness.to_bits());bits
}
fn flat(source:&CompactGeometry)->BoardWorld {
    let triangles=(0..source.triangle_count()).filter_map(|id|{
        let p=source.raw(id);let n=(p[1]-p[0]).cross(p[2]-p[0]);
        (n.length_squared()>1e-16).then_some(p.map(|p|p.to_array()))
    }).collect();
    skate_host::bridge::imported_collision_world(triangles,material()).unwrap()
}
#[test]
fn global_lazy_adjacency_matches_full_original_compiler() {
    let source=fixture();let reference=flat(&source);let mut ordinal=0;
    for id in 0..source.triangle_count() {
        if let Some(actual)=source.triangle(id,material()) {
            assert_eq!(bits(actual),bits(reference.triangle(ordinal).unwrap()),"source triangle{id}");ordinal+=1;
        }
    }
    assert_eq!(ordinal,reference.triangle_count());
}
fn contacts(values:&[BoardCollision])->Vec<(String,Vec<u32>)> {
    values.iter().map(|v|{
        let c=v.contact;let mut bits=vec![c.tag];
        for p in [c.position_on_a,c.position_on_b,c.normal]{bits.extend([p.x,p.y,p.z].map(f32::to_bits));}
        bits.extend([c.static_friction,c.dynamic_friction,c.restitution].map(f32::to_bits));(format!("{:?}",v.body_a),bits)
    }).collect()
}
#[test]
fn compact_queries_and_contacts_match_original_indexed_world() {
    let source=fixture();let mut reference=flat(&source);
    let mut compact=BoardWorld::from_source(source,material()).unwrap();compact.enable_imported_floor_seams();
    let query=WorldContactSettings{volume_padding:0.02,maximum_separating_distance:0.2,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false};
    let retention=ContactRetentionSettings{capacity:50,duplicate_distance_squared:0.000001,deferred_reduction:false};
    let mut hits=0;
    for x in -12..15 {for z in -12..15 {
        let start=Vector3::new(x as f32*0.75,4.,z as f32*0.75);let end=Vector3::new(start.x,-4.,start.z);
        assert_eq!(compact.query_swept_line(start,end,0.03),reference.query_swept_line(start,end,0.03));
        let volume=BoardWorldVolume{body:CollisionBody::Attached(0),primitive:ContactPrimitive::Sphere(Sphere{center:Vector3::new(start.x,0.08,start.z),radius:0.1}),
            linear_velocity:Vector3::new(0.,-1.,0.),material:material()};
        let expected=contacts(reference.query_primitives(&[volume],query,retention));hits+=expected.len();
        assert_eq!(contacts(compact.query_primitives(&[volume],query,retention)),expected,"contact at{x}/{z}");
    }}
    assert!(hits>0);
}
