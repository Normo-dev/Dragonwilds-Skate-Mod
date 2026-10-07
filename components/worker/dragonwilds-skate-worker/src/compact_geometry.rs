//! Anchored Skate view of immutable instanced host geometry. Adjacency uses
//! the same global 1mm weld/incident-face rules as skate_world::collision_world.
use crate::compact_world::CompactWorld;
use bevy::math::Vec3;
use skate_core::{math::Vector3, physics::{board_world::{WorldTriangle,
    geometry_source::WorldGeometry, query_metadata::{Bounds,QueryMesh,QueryMetadata,QueryPool}},
    collision::TriangleFeature, contact::RetailContactMaterial, drive_frames::RetailAffineTransform}};
use std::{collections::{HashMap,VecDeque},ops::Range,sync::{Arc,Mutex}};

const CACHE_TRIANGLES:usize=65_536;
type Key=[i64;3];
type Prepared=(u32,[f32;3]);
#[derive(Default)]
struct Cache { values:HashMap<usize,Option<Prepared>>,fifo:VecDeque<usize> }
pub struct CompactGeometry {
    pub world:Arc<CompactWorld>,pub anchor:[f64;3],metadata:QueryMetadata,margin:f32,cache:Mutex<Cache>,
}
fn core(v:Vec3)->Vector3 { Vector3::new(v.x,v.y,v.z) }
fn weld(v:Vec3)->Key {
    let inverse=1.0/f64::from(0.001_f32);
    v.to_array().map(|x|(f64::from(x)*inverse).round() as i64)
}
impl CompactGeometry {
    pub fn new(world:Arc<CompactWorld>,anchor:[f64;3])->Result<Self,String> {
        if !anchor.iter().all(|x|x.is_finite()){return Err("Invalid whole-world anchor".into());}
        let convert=|p:[f64;3]|Vec3::new(((p[0]-anchor[0])*0.01) as f32,
            ((p[2]-anchor[2])*0.01) as f32,-(((p[1]-anchor[1])*0.01) as f32));
        let mut meshes=Vec::with_capacity(world.groups().len());let mut margin=0f32;
        for group in world.groups(){
            let a=convert(group.bounds.min);let b=convert(group.bounds.max);
            let lo=a.min(b);let hi=a.max(b);
            if !lo.is_finite()||!hi.is_finite(){return Err("Whole-world bounds overflow source coordinates".into());}
            margin=margin.max((hi-lo).max_element()*(2.*f32::from_bits(0x3727_c5ac)));
            meshes.push(QueryMesh{triangle_range:group.triangle_range.clone(),local_to_world:RetailAffineTransform::IDENTITY,
                world_to_local:RetailAffineTransform::IDENTITY,local_bounds:Bounds{min:core(lo),max:core(hi)},
                matching_group:-1,rejection_flags:0,geometry:0,pool:QueryPool::Ground});
        }
        Ok(Self{world,anchor,metadata:QueryMetadata{packed_surfaces:vec![],meshes,static_edges:vec![],island_flags:0},margin,cache:Mutex::new(Cache::default())})
    }
    pub fn raw(&self,index:usize)->[Vec3;3] {
        self.world.raw_triangle(index).map(|p|Vec3::new(((p[0]-self.anchor[0])*0.01) as f32,
            ((p[2]-self.anchor[2])*0.01) as f32,-(((p[1]-self.anchor[1])*0.01) as f32)))
    }
    fn valid_raw(&self,index:usize)->Option<([Vec3;3],Vec3)> {
        let p=self.raw(index);let cross=(p[1]-p[0]).cross(p[2]-p[0]);
        // Same source-f32 degeneracy gate as MapCache.build, before adjacency.
        if cross.length_squared()<=1e-16 || !p.iter().all(|p|p.is_finite()){return None;}
        Some((p,cross.try_normalize()?))
    }
    pub fn unreal_bounds(&self,b:Bounds)->([f64;3],[f64;3]) {
        let lo=[b.min.x,b.min.y,b.min.z];let hi=[b.max.x,b.max.y,b.max.z];
        // Include f32 source rounding before inverse anchor conversion. Bounds
        // in the immutable reader are f64 host coordinates, not rounded vertices.
        let scale=lo.into_iter().chain(hi).map(f32::abs).fold(1.,f32::max);
        let pad=f64::from(scale*f32::EPSILON*8.+0.000001)*100.;
        ([self.anchor[0]+f64::from(lo[0])*100.-pad,self.anchor[1]-f64::from(hi[2])*100.-pad,self.anchor[2]+f64::from(lo[1])*100.-pad],
         [self.anchor[0]+f64::from(hi[0])*100.+pad,self.anchor[1]-f64::from(lo[2])*100.+pad,self.anchor[2]+f64::from(hi[1])*100.+pad])
    }
    fn prepare(&self,index:usize)->Option<Prepared> {
        let (points,normal)=self.valid_raw(index)?;let keys=points.map(weld);
        let mut ids=Vec::new();
        for p in points {
            let b=Bounds{min:core(p-Vec3::splat(0.0011)),max:core(p+Vec3::splat(0.0011))};
            let (lo,hi)=self.unreal_bounds(b);ids.extend(self.world.candidate_triangles(lo,hi));
        }
        ids.push(index);ids.sort_unstable();ids.dedup();
        let incident:Vec<_>=ids.into_iter().filter_map(|id|{
            let (p,n)=self.valid_raw(id)?;let k=p.map(weld);
            k.iter().any(|k|keys.contains(k)).then_some((id,p,n,k))
        }).collect();
        let representatives=keys.map(|key|incident.iter().find_map(|(_,p,_,k)|
            k.iter().position(|&k|k==key).map(|at|p[at])).unwrap());
        let mut flags=TriangleFeature::ONE_SIDED|TriangleFeature::USE_EDGE_COSINES|0xe0;
        let mut cosines=[1.;3];
        for edge in 0..3 {
            let (a,b)=(keys[edge],keys[(edge+1)%3]);
            let mut other=None;let mut best=f32::NEG_INFINITY;
            for &(id,_,n,k) in &incident {
                if id==index {continue;}
                for e in 0..3 {
                    if k[e]==b && k[(e+1)%3]==a {
                        let dot=normal.dot(n);
                        // max_by in the original incident list retains the last
                        // equal entry; canonical IDs preserve that exact order.
                        if dot.total_cmp(&best).is_ge(){best=dot;other=Some(n);}
                    }
                }
            }
            if let Some(n)=other {
                let cosine=normal.dot(n).clamp(-1.,1.);
                let orientation=(representatives[(edge+1)%3]-representatives[edge]).dot(normal.cross(n));
                cosines[edge]=cosine;
                if orientation<=-1e-6 || cosine>=1.-1e-5 {flags&=!(0x20<<edge);}
            }
        }
        for corner in 0..3 {
            let faces:Vec<_>=incident.iter().filter(|(_,_,_,k)|k.contains(&keys[corner])).collect();
            let reference=faces[0].2;
            if faces.iter().all(|(_,_,n,_)|(reference.dot(*n)-1.).abs()<=0.01){flags|=0x200<<corner;}
        }
        Some((flags,cosines))
    }
    fn prepared(&self,index:usize)->Option<Prepared> {
        if let Some(value)=self.cache.lock().unwrap().values.get(&index).copied(){return value;}
        let value=self.prepare(index);let mut cache=self.cache.lock().unwrap();
        if !cache.values.contains_key(&index) {
            while cache.values.len()>=CACHE_TRIANGLES {
                if let Some(old)=cache.fifo.pop_front(){cache.values.remove(&old);}
            }
            cache.values.insert(index,value);cache.fifo.push_back(index);
        }
        value
    }
}
impl WorldGeometry for CompactGeometry {
    fn validate(&self)->Result<(), &'static str> {
        if self.world.triangle_count()==0 || self.metadata.meshes.is_empty() {
            return Err("Whole-world geometry has no complete canonical ranges");
        }
        let mut end=0;
        for mesh in &self.metadata.meshes {
            if mesh.triangle_range.start<end || mesh.triangle_range.end<=mesh.triangle_range.start
                || mesh.triangle_range.end>self.world.triangle_count() {return Err("Invalid whole-world canonical range");}
            end=mesh.triangle_range.end;
        }
        Ok(())
    }
    fn triangle_count(&self)->usize {self.world.triangle_count()}
    fn metadata(&self)->&QueryMetadata {&self.metadata}
    fn triangle(&self,index:usize,material:RetailContactMaterial)->Option<WorldTriangle> {
        let (flags,cosines)=self.prepared(index)?;
        WorldTriangle::from_vertices(self.raw(index).map(core),material,0,flags,cosines,0.)
    }
    fn triangle_bounds(&self,index:usize)->Bounds {Bounds::from_points(self.raw(index).map(core)).unwrap()}
    fn packed_surface(&self,_:usize)->u16 {0}
    fn candidate_ranges(&self,bounds:Option<Bounds>)->Vec<Range<usize>> {
        static DIAGNOSTICS:std::sync::OnceLock<bool>=std::sync::OnceLock::new();
        let diagnostic=*DIAGNOSTICS.get_or_init(||std::env::var_os("SKATE_WORLD_QUERY_DIAGNOSTICS").is_some());
        let Some(bounds)=bounds else{
            if diagnostic {eprintln!("WORLD_QUERY_UNBOUNDED triangles={}",self.world.triangle_count());}
            return self.metadata.meshes.iter().map(|m|m.triangle_range.clone()).collect()
        };
        let (lo,hi)=self.unreal_bounds(bounds);let ids=self.world.candidate_triangles(lo,hi);
        if diagnostic && ids.len()>4096 {eprintln!("WORLD_QUERY_LARGE triangles={} min={lo:?} max={hi:?}",ids.len());}
        let mut ranges:Vec<Range<usize>>=Vec::new();
        for id in ids {
            if let Some(last)=ranges.last_mut().filter(|last|last.end==id){last.end+=1;}
            else{ranges.push(id..id+1);}
        }
        ranges
    }
    fn candidate_mesh_indices(&self,bounds:Option<Bounds>)->Vec<usize> {
        bounds.map_or_else(||(0..self.metadata.meshes.len()).collect(),|b|{
            let (lo,hi)=self.unreal_bounds(b);self.world.candidate_groups(lo,hi)})
    }
    fn maximum_fatness(&self)->f32 {0.}
    fn maximum_triangle_margin(&self)->f32 {self.margin}
}
