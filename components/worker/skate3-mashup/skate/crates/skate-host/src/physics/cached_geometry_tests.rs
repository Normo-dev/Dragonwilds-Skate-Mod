use super::*;
use skate_core::{math::Vector3, physics::{board_world::{BoardWorld, BoardWorldVolume, ContactRetentionSettings,
    query_metadata::{QueryMesh, QueryPool}}, board_step::CollisionBody,
    collision::{Sphere, WorldContactSettings}, drive_frames::RetailAffineTransform,
    world_contact::ContactPrimitive}};
use std::sync::atomic::{AtomicUsize, Ordering};

struct Source { y: f32, metadata: QueryMetadata, triangles: AtomicUsize, bounds: AtomicUsize }
impl Source {
    fn new(y: f32) -> Arc<Self> {
        Arc::new(Self { y, triangles: AtomicUsize::new(0), bounds: AtomicUsize::new(0),
            metadata: QueryMetadata { packed_surfaces: vec![7, 9, 11, 13], static_edges: vec![], island_flags: 0,
                meshes: vec![QueryMesh { triangle_range: 0..4, local_to_world: RetailAffineTransform::IDENTITY,
                    world_to_local: RetailAffineTransform::IDENTITY, local_bounds: Bounds { min: Vector3::new(-1.,y,-1.), max: Vector3::new(1.,y,1.) },
                    matching_group: -1, rejection_flags: 0, geometry: 17, pool: QueryPool::Ground }] } })
    }
    fn vertices(&self, index: usize) -> [Vector3; 3] {
        let (a,b,c) = (Vector3::new(-1., self.y,-1.),Vector3::new(-1.,self.y,1.),Vector3::new(1.,self.y,1.));
        match index { 0|1 => [a,b,c], 2 => [Vector3::new(0.,self.y,0.);3], 3 => [a,c,Vector3::new(1.,self.y,-1.)], _=>panic!("source index") }
    }
}
impl WorldGeometry for Source {
    fn validate(&self)->Result<(), &'static str>{Ok(())}
    fn triangle_count(&self)->usize{4}
    fn metadata(&self)->&QueryMetadata{&self.metadata}
    fn triangle(&self,index:usize,material:RetailContactMaterial)->Option<WorldTriangle>{
        self.triangles.fetch_add(1,Ordering::Relaxed);
        WorldTriangle::from_vertices(self.vertices(index),material,index as u32,8,[1.;3],0.)
    }
    fn triangle_bounds(&self,index:usize)->Bounds{
        self.bounds.fetch_add(1,Ordering::Relaxed);Bounds::from_points(self.vertices(index)).unwrap()
    }
    fn packed_surface(&self,index:usize)->u16{self.metadata.packed_surfaces[index]}
    fn candidate_ranges(&self,_:Option<Bounds>)->Vec<Range<usize>>{vec![0..2,2..4]}
    fn candidate_mesh_indices(&self,_:Option<Bounds>)->Vec<usize>{vec![0]}
    fn maximum_fatness(&self)->f32{f32::from_bits(0x80000000)}
    fn maximum_triangle_margin(&self)->f32{0.0001}
}
fn material()->RetailContactMaterial{RetailContactMaterial{static_friction:0.7,dynamic_friction:0.4,restitution:0.2}}
fn triangle_bits(value:Option<WorldTriangle>)->Option<Vec<u32>>{
    value.map(|v|{let t=v.triangle;let mut out=vec![v.tag,t.feature.flags];
        for p in t.vertices.into_iter().chain([t.feature.normal]).chain(t.feature.edges){out.extend([p.x,p.y,p.z].map(f32::to_bits));}
        out.extend(t.edge_lengths.map(f32::to_bits));out.extend(t.feature.edge_cosines.map(f32::to_bits));out.push(t.fatness.to_bits());out.extend(material_bits(v.material));out})
}
fn bound_bits(value:Bounds)->[u32;6]{[value.min.x,value.min.y,value.min.z,value.max.x,value.max.y,value.max.z].map(f32::to_bits)}

#[test]
fn exact_values_material_bits_and_degenerate_results_are_memoized(){
    let source=Source::new(0.);let memo=Geometry::new(source.clone(),8);
    let materials=[material(),RetailContactMaterial{static_friction:-0.,..material()},
        RetailContactMaterial{static_friction:f32::from_bits(0x7fc00001),..material()},
        RetailContactMaterial{static_friction:f32::from_bits(0x7fc00002),..material()}];
    for m in materials {for id in 0..4{
        let expected=triangle_bits(source.triangle(id,m));let bounds=bound_bits(source.triangle_bounds(id));
        assert_eq!(triangle_bits(memo.triangle(id,m)),expected);assert_eq!(bound_bits(memo.triangle_bounds(id)),bounds);
        let before=(source.triangles.load(Ordering::Relaxed),source.bounds.load(Ordering::Relaxed));
        for _ in 0..3 {assert_eq!(triangle_bits(memo.triangle(id,m)),expected);assert_eq!(bound_bits(memo.triangle_bounds(id)),bounds);}
        assert_eq!(before,(source.triangles.load(Ordering::Relaxed),source.bounds.load(Ordering::Relaxed)));
    }}
    assert_eq!(memo.cache.lock().unwrap().entries.len(),4);
}

#[test]
fn eviction_and_new_source_epoch_never_reuse_stale_values(){
    let source=Source::new(0.);let memo=Geometry::new(source.clone(),2);
    for id in 0..4{memo.triangle(id,material());memo.triangle_bounds(id);}
    assert_eq!(memo.cache.lock().unwrap().entries.len(),2);
    assert!(!memo.cache.lock().unwrap().entries.contains_key(&0));
    let before=source.triangles.load(Ordering::Relaxed);
    assert_eq!(triangle_bits(memo.triangle(0,material())),triangle_bits(source.triangle(0,material())));
    assert_eq!(source.triangles.load(Ordering::Relaxed),before+2);
    let next=Geometry::new(Source::new(2.),2);
    assert_ne!(triangle_bits(memo.triangle(0,material())),triangle_bits(next.triangle(0,material())));
    assert_ne!(bound_bits(memo.triangle_bounds(0)),bound_bits(next.triangle_bounds(0)));
}

#[test]
fn traversal_metadata_lines_and_retained_contact_order_match_source(){
    let source=Source::new(0.);let memo=Arc::new(Geometry::new(source.clone(),8));
    assert!(std::ptr::eq(source.metadata(),memo.metadata()));
    assert_eq!(memo.candidate_ranges(None),source.candidate_ranges(None));
    assert_eq!(memo.candidate_mesh_indices(None),source.candidate_mesh_indices(None));
    assert_eq!(memo.maximum_fatness().to_bits(),source.maximum_fatness().to_bits());
    assert_eq!(memo.maximum_triangle_margin().to_bits(),source.maximum_triangle_margin().to_bits());
    for id in 0..4{assert_eq!(memo.packed_surface(id),source.packed_surface(id));}
    let mut before=BoardWorld::from_source(source,material()).unwrap();let mut after=BoardWorld::from_source(memo,material()).unwrap();
    let query=WorldContactSettings{volume_padding:0.02,maximum_separating_distance:0.2,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false};
    let retention=ContactRetentionSettings{capacity:50,duplicate_distance_squared:0.000001,deferred_reduction:false};
    let (mut line_hits,mut contact_rows)=(0,0);
    for _ in 0..3 {for x in -3..4{for z in -3..4{
        let start=Vector3::new(x as f32*0.25,2.,z as f32*0.25);let end=Vector3::new(start.x,-2.,start.z);
        let a=before.query_swept_line(start,end,0.03).unwrap();let b=after.query_swept_line(start,end,0.03).unwrap();
        assert_eq!(a,b);if let Some(hit)=a{line_hits+=1;assert_ne!(hit.tag,1,"strict first equal triangle retained");}
        let volume=BoardWorldVolume{body:CollisionBody::Attached(0),primitive:ContactPrimitive::Sphere(Sphere{center:Vector3::new(start.x,0.08,start.z),radius:0.1}),linear_velocity:Vector3::new(0.,-1.,0.),material:material()};
        let expected=before.query_primitives(&[volume],query,retention);contact_rows+=expected.len();
        assert_eq!(format!("{expected:?}"),format!("{:?}",after.query_primitives(&[volume],query,retention)));
    }}}
    assert!(line_hits>0);assert!(contact_rows>0);
}
