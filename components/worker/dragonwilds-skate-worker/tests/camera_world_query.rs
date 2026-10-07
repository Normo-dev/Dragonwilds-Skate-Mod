//! Host ray-boundary regression for the actual zero-horizontal stock predictor.
#[path="../../skate3-mashup/skate/crates/skate-host/src/camera/world_query.rs"]
mod world_query;
use skate_core::{camera::{DropPredictor,DropSettings,DropCollisionProvider,FatLine,FatLineResult},
    math::Vector3,physics::{board_world::{BoardWorld,WorldTriangle,geometry_source::WorldGeometry,
        query_metadata::{Bounds,QueryMetadata}},contact::RetailContactMaterial}};
use std::{ops::Range,sync::Arc};

struct UnqueriedSource(QueryMetadata);
impl WorldGeometry for UnqueriedSource {
    fn validate(&self)->Result<(), &'static str>{Ok(())}
    fn triangle_count(&self)->usize{76_537_305}
    fn metadata(&self)->&QueryMetadata{&self.0}
    fn triangle(&self,_:usize,_:RetailContactMaterial)->Option<WorldTriangle>{panic!("invalid camera ray materialized world geometry")}
    fn triangle_bounds(&self,_:usize)->Bounds{panic!("invalid camera ray requested triangle bounds")}
    fn packed_surface(&self,_:usize)->u16{panic!("invalid camera ray requested a surface")}
    fn candidate_ranges(&self,_:Option<Bounds>)->Vec<Range<usize>>{panic!("invalid camera ray entered broadphase")}
    fn candidate_mesh_indices(&self,_:Option<Bounds>)->Vec<usize>{panic!("invalid camera ray entered mesh broadphase")}
    fn maximum_fatness(&self)->f32{0.}
    fn maximum_triangle_margin(&self)->f32{0.}
}
fn material()->RetailContactMaterial{RetailContactMaterial{static_friction:0.7,dynamic_friction:0.4,restitution:0.2}}
fn unqueried()->BoardWorld{BoardWorld::from_source(Arc::new(UnqueriedSource(QueryMetadata{
    packed_surfaces:vec![],meshes:vec![],static_edges:vec![],island_flags:0})),material()).unwrap()}

#[test]
fn invalid_xyz_and_overflowing_delta_never_enumerate_complete_world(){
    let world=unqueried();
    for value in [f32::NAN,f32::INFINITY,f32::NEG_INFINITY]{
        for lane in 0..3{
            let mut bad=[0.;4];bad[lane]=value;
            assert_eq!(world_query::line(&world,bad,[1.;4],0.01).unwrap().hit,0);
            assert_eq!(world_query::line(&world,[1.;4],bad,0.01).unwrap().hit,0);
        }
    }
    assert_eq!(world_query::line(&world,[-f32::MAX;4],[f32::MAX;4],0.01).unwrap().hit,0);
    assert!(world_query::line(&world,[0.;4],[1.;4],f32::NAN).is_err());
}

#[test]
fn unused_w_remains_ignored_and_valid_camera_hits_keep_source_face(){
    let t=WorldTriangle::from_vertices([Vector3::new(-2.,0.,-2.),Vector3::new(-2.,0.,2.),
        Vector3::new(2.,0.,-2.)],material(),23,0,[1.;3],0.).unwrap();
    let world=BoardWorld::new(vec![t]);
    let expected=world_query::line(&world,[-0.5,1.,-0.5,0.],[-0.5,-1.,-0.5,0.],0.01).unwrap();
    assert_eq!(expected.hit,1);assert_eq!(expected.surface,23);
    let actual=world_query::line(&world,[-0.5,1.,-0.5,f32::NAN],[-0.5,-1.,-0.5,f32::INFINITY],0.01).unwrap();
    assert_eq!(actual,expected);
}

struct HostDrop<'a>(&'a BoardWorld,usize);
impl DropCollisionProvider for HostDrop<'_>{
    fn query(&mut self,lines:[FatLine;10],_:u32)->Result<[FatLineResult;10],String>{
        let mut results=[world_query::no_hit();10];
        for (line,result) in lines.into_iter().zip(&mut results){
            assert!(!line.end[..3].iter().all(|v|v.is_finite()),"fixture must exercise original invalid predictor path");
            *result=world_query::line(self.0,line.start,line.end,line.radius)?;self.1+=1;
        }Ok(results)
    }
}
#[test]
fn original_vertical_drop_predictor_cannot_turn_invalid_rays_into_world_scan(){
    let world=unqueried();let mut query=HostDrop(&world,0);let mut predictor=DropPredictor::new();
    let settings=DropSettings{minimum_test_distance:1.,total_test_time:1.,maximum_test_distance:10.,maximum_drop_distance:5.};
    let transform=[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[-4812.,-428.,4771.,0.]];
    let velocity=DropPredictor::prediction_velocity(false,transform,[0.,-2.,0.,0.],settings);
    assert_eq!(velocity,[0.,-2.,0.,0.]);
    for _ in 0..3{predictor.update(transform[3],velocity,true,false,1,settings,&mut query).unwrap();
        assert!(predictor.elevation.is_finite());assert!(predictor.target_elevation.is_finite());}
    assert_eq!(query.1,30);
}
