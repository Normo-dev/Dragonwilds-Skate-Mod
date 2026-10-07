//! Exact host-adapter regression: original query arithmetic/order remains imported.
#[allow(dead_code)]
#[path="../../skate3-mashup/skate/crates/skate-host/src/physics/offboard/contact_toolkit/world.rs"]
mod optimized;
#[allow(dead_code)]
#[path="support/offboard_world_reference.rs"]
mod reference;
use skate_core::{math::Vector3,physics::{board_world::{BoardWorld,WorldTriangle,
    geometry_source::WorldGeometry,query_metadata::{Bounds,QueryMesh,QueryMetadata,QueryPool}},
    contact::RetailContactMaterial,drive_frames::RetailAffineTransform},
    player::offboard::{contact_toolkit::{Input,ProbeLayout,QueryResults,Scene},ground_query::{Line,LineHit}}};
use std::{ops::Range,sync::{Arc,atomic::{AtomicUsize,Ordering}}};
use serde_json::{Value,json};
fn material()->RetailContactMaterial {RetailContactMaterial{static_friction:0.7,dynamic_friction:0.4,restitution:0.2}}
struct Source {triangles:Vec<WorldTriangle>,metadata:QueryMetadata,prepares:AtomicUsize,ranges:AtomicUsize}
impl Source {fn reset(&self){self.prepares.store(0,Ordering::Relaxed);self.ranges.store(0,Ordering::Relaxed);}fn count(&self)->(usize,usize){(self.prepares.load(Ordering::Relaxed),self.ranges.load(Ordering::Relaxed))}}
impl WorldGeometry for Source {
 fn validate(&self)->Result<(),&'static str>{Ok(())}
 fn triangle_count(&self)->usize{self.triangles.len()}
 fn metadata(&self)->&QueryMetadata{&self.metadata}
 fn triangle(&self,i:usize,_:RetailContactMaterial)->Option<WorldTriangle>{self.prepares.fetch_add(1,Ordering::Relaxed);self.triangles.get(i).copied()}
 fn triangle_bounds(&self,i:usize)->Bounds{Bounds::from_points(self.triangles[i].triangle.vertices).unwrap()}
 fn packed_surface(&self,i:usize)->u16{self.metadata.packed_surfaces[i]}
 fn candidate_ranges(&self,b:Option<Bounds>)->Vec<Range<usize>>{self.ranges.fetch_add(1,Ordering::Relaxed);self.metadata.meshes.iter().filter(|m|b.is_none_or(|b|m.local_bounds.overlaps(b))).map(|m|m.triangle_range.clone()).collect()}
 fn candidate_mesh_indices(&self,b:Option<Bounds>)->Vec<usize>{self.metadata.meshes.iter().enumerate().filter(|(_,m)|b.is_none_or(|b|m.local_bounds.overlaps(b))).map(|(i,_)|i).collect()}
 fn maximum_fatness(&self)->f32{0.}
 fn maximum_triangle_margin(&self)->f32{0.001}
}
fn fixture(spec:&[(QueryPool,i32,u32)],flags:u32,sloped:bool)->(BoardWorld,Arc<Source>){
 let triangles:Vec<_>=spec.iter().enumerate().map(|(i,_)|{let slope=if sloped{(i%5)as f32*0.025}else{0.};
  WorldTriangle::from_vertices([Vector3::new(-4.,-4.*slope,-4.),Vector3::new(-4.,-4.*slope,4.),Vector3::new(4.,4.*slope,0.)],material(),i as u32,0xe0,[1.;3],0.).unwrap()}).collect();
 let meshes=spec.iter().enumerate().map(|(i,&(pool,group,reject))|QueryMesh{triangle_range:i..i+1,
  local_to_world:RetailAffineTransform::IDENTITY,world_to_local:RetailAffineTransform::IDENTITY,
  local_bounds:Bounds::from_points(triangles[i].triangle.vertices).unwrap(),matching_group:group,
  rejection_flags:reject,geometry:1000+i as u32,pool}).collect();
 let source=Arc::new(Source{triangles,metadata:QueryMetadata{packed_surfaces:(0..spec.len()).map(|i|(0x400+i)as u16).collect(),meshes,static_edges:vec![],island_flags:flags},prepares:AtomicUsize::new(0),ranges:AtomicUsize::new(0)});
 (BoardWorld::from_source(source.clone(),material()).unwrap(),source)
}
fn vbits(v:Vector3)->[u32;3]{[v.x.to_bits(),v.y.to_bits(),v.z.to_bits()]}
fn line_bits(hits:Vec<Option<LineHit>>)->Value {json!(hits.into_iter().map(|h|h.map(|h|(vbits(h.position),vbits(h.face_normal),h.fraction.to_bits(),h.packed_surface))).collect::<Vec<_>>())}
fn result_bits(r:QueryResults)->Value {json!({
 "trajectories":r.trajectories.map(|q|json!([q.contact_position.map(f32::to_bits),q.contact_normal.map(f32::to_bits),q.landing_normal.map(f32::to_bits),q.contact_time.to_bits(),q.contact_transform.map(|v|v.map(f32::to_bits)),q.contact_frame,q.surface,q.geometry])),
 "lines":r.lines.into_iter().map(|h|h.map(|h|json!([h.position.map(f32::to_bits),h.normal.map(f32::to_bits),h.fraction.to_bits(),h.surface,h.geometry,h.mesh_frame.map(|v|v.map(f32::to_bits))]))).collect::<Vec<_>>(),
 "edges":r.edges.into_iter().map(|e|e.map(|v|v.map(f32::to_bits))).collect::<Vec<_>>()})}
fn input(x:f32,yaw:f32)->Input {let(s,c)=yaw.sin_cos();Input{position:[x,0.,0.,0.],forward:[s,0.,c,0.],up:[0.,1.,0.,0.],right:[c,0.,-s,0.],velocity:[s,0.,c,0.],animation_up:[0.,1.,0.,0.],animation_right:[c,0.,-s,0.]}}
#[test]
fn empty_island_and_filtered_pools_do_not_prepare_triangles(){
 let(w,s)=fixture(&[(QueryPool::Ground,7,0)],0,false);
 let line=Line{start:Vector3::new(0.,1.,0.),end:Vector3::new(0.,-1.,0.),radius:0.};
 s.reset();let expected=reference::StaticScene::new(&w).unwrap().lines(&[line],7).unwrap();let old=s.count();
 s.reset();let actual=optimized::StaticScene::new(&w).unwrap().lines(&[line],7).unwrap();let new=s.count();
 assert_eq!(line_bits(actual),line_bits(expected));
 // Ground: one preparation retained; Island: no triangle broadphase/preparation.
 assert_eq!(old,(3,2));assert_eq!(new,(1,1));
 s.reset();assert!(optimized::StaticScene::new(&w).unwrap().lines(&[line],99).unwrap()[0].is_none());assert_eq!(s.count(),(0,0));
 let(w,s)=fixture(&[(QueryPool::Ground,7,0x6000)],0,false);
 let batch=ProbeLayout::stock().prepare(input(0.,0.),7);
 s.reset();let expected=reference::StaticScene::new(&w).unwrap().execute(&batch).unwrap();assert!(s.count().0>0);
 s.reset();let actual=optimized::StaticScene::new(&w).unwrap().execute(&batch).unwrap();assert_eq!(s.count(),(0,0));
 assert_eq!(result_bits(actual),result_bits(expected));
}
#[test]
fn complete_batches_keep_pool_group_rejection_ties_and_nearby_cap_bit_exact(){
 for flags in [0,3] {for sloped in [false,true] {
  let spec:Vec<_>=(0..84).map(|i|{let pool=match i%3{0=>QueryPool::Ground,1=>QueryPool::Island,_=>QueryPool::Conditional};
   (pool,if i%7==0{17}else{-1},if i%11==0{0x6000}else{0})}).collect();
  let(w,_)=fixture(&spec,flags,sloped);
  for group in [-1,7,17] {for (x,yaw)in[(0.,0.),(0.2,0.3),(-0.3,-0.7),(10.,0.)] {
   let batch=ProbeLayout::stock().prepare(input(x,yaw),group);assert_eq!(batch.lines.len(),44);
   let expected=reference::StaticScene::new(&w).unwrap().execute(&batch).unwrap();
   let actual=optimized::StaticScene::new(&w).unwrap().execute(&batch).unwrap();
   assert_eq!(result_bits(actual),result_bits(expected),"flags={flags} sloped={sloped} group={group} x={x} yaw={yaw}");
  }}
 }}
 // More than64 overlapping eligible faces in one pool exercise ordered cap.
 let(w,_)=fixture(&vec![(QueryPool::Ground,-1,0);84],0,true);
 let batch=ProbeLayout::stock().prepare(input(0.,0.),-1);
 assert_eq!(result_bits(optimized::StaticScene::new(&w).unwrap().execute(&batch).unwrap()),result_bits(reference::StaticScene::new(&w).unwrap().execute(&batch).unwrap()));
 let(w,_)=fixture(&[(QueryPool::Ground,-1,0),(QueryPool::Island,-1,0),(QueryPool::Conditional,-1,0)],3,false);
 let line=Line{start:Vector3::new(0.,1.,0.),end:Vector3::new(0.,-1.,0.),radius:0.};
 assert_eq!(optimized::StaticScene::new(&w).unwrap().lines(&[line],-1).unwrap()[0].unwrap().packed_surface,0x400);
}
#[test]
fn invalid_and_zero_length_requests_keep_original_results(){
 let(w,_)=fixture(&[(QueryPool::Ground,-1,0)],0,false);
 for (start,end,radius)in[(Vector3::ZERO,Vector3::ZERO,0.),(Vector3::new(f32::NAN,1.,0.),Vector3::ZERO,0.),(Vector3::ZERO,Vector3::new(0.,1.,0.),-1.)] {
  let line=Line{start,end,radius};
  let a=optimized::StaticScene::new(&w).unwrap().lines(&[line],-1);
  let b=reference::StaticScene::new(&w).unwrap().lines(&[line],-1);
  assert_eq!(a.is_err(),b.is_err());if let(Ok(a),Ok(b))=(a,b){assert_eq!(line_bits(a),line_bits(b));}
 }
}
