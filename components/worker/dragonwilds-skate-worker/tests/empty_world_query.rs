#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
use compact_geometry::CompactGeometry;
use compact_world::CompactWorld;
use std::{ops::Range,path::PathBuf,sync::{Arc,atomic::{AtomicUsize,Ordering}}};
use skate_core::{math::Vector3,physics::{board_world::{BoardWorld,WorldTriangle,geometry_source::WorldGeometry,
    query_metadata::{Bounds,QueryMetadata},BoardWorldVolume,ContactRetentionSettings},
    contact::RetailContactMaterial,world_contact::ContactPrimitive,collision::{Sphere,WorldContactSettings},board_step::{BoardCollision,CollisionBody}}};

struct Counted { inner:CompactGeometry,reads:AtomicUsize }
impl Counted { fn read(&self){self.reads.fetch_add(1,Ordering::Relaxed);} }
impl WorldGeometry for Counted {
    fn validate(&self)->Result<(),&'static str>{self.inner.validate()}
    fn triangle_count(&self)->usize{self.read();self.inner.triangle_count()}
    fn metadata(&self)->&QueryMetadata{self.read();self.inner.metadata()}
    fn triangle(&self,i:usize,m:RetailContactMaterial)->Option<WorldTriangle>{self.read();self.inner.triangle(i,m)}
    fn triangle_bounds(&self,i:usize)->Bounds{self.read();self.inner.triangle_bounds(i)}
    fn packed_surface(&self,i:usize)->u16{self.read();self.inner.packed_surface(i)}
    fn candidate_ranges(&self,b:Option<Bounds>)->Vec<Range<usize>>{self.read();self.inner.candidate_ranges(b)}
    fn candidate_mesh_indices(&self,b:Option<Bounds>)->Vec<usize>{self.read();self.inner.candidate_mesh_indices(b)}
    fn maximum_fatness(&self)->f32{self.inner.maximum_fatness()}
    fn maximum_triangle_margin(&self)->f32{self.inner.maximum_triangle_margin()}
}
fn material()->RetailContactMaterial{RetailContactMaterial{static_friction:0.7,dynamic_friction:0.4,restitution:0.2}}
fn world()->(BoardWorld,Arc<Counted>){
    let path=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture/export/manifest.json");
    let source=Arc::new(Counted{inner:CompactGeometry::new(Arc::new(CompactWorld::load(&path).unwrap()),[13.25,-17.125,4.75]).unwrap(),reads:AtomicUsize::new(0)});
    (BoardWorld::from_source(source.clone(),material()).unwrap(),source)
}
fn query()->WorldContactSettings{WorldContactSettings{volume_padding:0.02,maximum_separating_distance:0.2,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false}}
fn retention()->ContactRetentionSettings{ContactRetentionSettings{capacity:50,duplicate_distance_squared:0.000001,deferred_reduction:false}}
fn volume(x:f32,z:f32)->BoardWorldVolume{BoardWorldVolume{body:CollisionBody::Attached(0),primitive:ContactPrimitive::Sphere(Sphere{center:Vector3::new(x,0.08,z),radius:0.1}),linear_velocity:Vector3::new(0.,-1.,0.),material:material()}}
fn bits(values:&[BoardCollision])->Vec<(String,Vec<u32>)>{values.iter().map(|v|{
    let c=v.contact;let mut bits=vec![c.tag];
    for p in[c.position_on_a,c.position_on_b,c.normal]{bits.extend([p.x,p.y,p.z].map(f32::to_bits));}
    bits.extend([c.static_friction,c.dynamic_friction,c.restitution].map(f32::to_bits));(format!("{:?}",v.body_a),bits)
}).collect()}
#[test]
fn empty_volumes_never_access_world_even_when_settings_unbounded(){
    let(mut world,source)=world();let mut settings=query();settings.volume_padding=f32::INFINITY;
    for q in[query(),settings]{
        source.reads.store(0,Ordering::Relaxed);
        assert!(world.query_primitives(&[],q,retention()).is_empty());
        assert_eq!(source.reads.load(Ordering::Relaxed),0);
        assert!(world.contacts().is_empty());assert_eq!(world.dropped_contacts(),0);
    }
}
#[test]
fn empty_volumes_clear_reused_contacts_and_leave_next_nonempty_query_unchanged(){
    let(mut reused,source)=world();let(mut fresh,_)=world();let mut hits=0;
    for x in -12..15{for z in -12..15{
        let v=volume(x as f32*0.75,z as f32*0.75);
        let expected=bits(fresh.query_primitives(&[v],query(),retention()));hits+=expected.len();
        assert_eq!(bits(reused.query_primitives(&[v],query(),retention())),expected);
        if !expected.is_empty(){
            let mut limited=retention();limited.capacity=0;
            reused.query_primitives(&[v],query(),limited);
            assert!(reused.dropped_contacts()>0);
        }
        source.reads.store(0,Ordering::Relaxed);
        assert!(reused.query_primitives(&[],query(),retention()).is_empty());
        assert_eq!(source.reads.load(Ordering::Relaxed),0);assert!(reused.contacts().is_empty());assert_eq!(reused.dropped_contacts(),0);
        assert_eq!(bits(reused.query_primitives(&[v],query(),retention())),expected);
    }}assert!(hits>0);
}
