//! Permanent original-example lip bake over shared whole-world geometry.
//! Candidate ownership, ray predicates, merging and chaining remain upstream.
use crate::{compact_geometry::CompactGeometry,compact_world::CompactWorld,example_rails::{self,EdgeCandidate,EdgeKey}};
use bevy::math::Vec3;
use std::{collections::{HashMap,HashSet,VecDeque},io::{Read,Write},path::Path,sync::Arc,time::Instant};

const VERSION:u32=3;
const HEADER:usize=120;
const RECORD:usize=48;
const CELL:f32=64.;
/// A verified eligible edge. A triangle ordinal belongs to one snapshot only;
/// original grind construction consumes these exact geometric values, never
/// the ephemeral candidate's ordinal. This record remains valid when unrelated
/// live instances are inserted before it in the canonical source order.
#[derive(Clone,Copy,Debug)]
pub struct Lip {pub a:Vec3,pub b:Vec3,pub normal:Vec3,pub centroid:Vec3}
impl From<EdgeCandidate> for Lip {
    fn from(e:EdgeCandidate)->Self {Self{a:e.a,b:e.b,normal:e.normal,centroid:e.centroid}}
}
pub struct LipBake {pub fingerprint:String,pub candidates:usize,pub lips:Vec<Lip>}
fn fingerprint(world:&CompactWorld)->&str {world.scene_fingerprint.as_deref().unwrap_or(&world.source_fingerprint)}
pub fn map_triangle(source:&CompactGeometry,id:usize)->[Vec3;3] {
    source.raw(id).map(|p|Vec3::new(p.x,-p.z,p.y)/0.0254)
}
fn valid_triangle(source:&CompactGeometry,id:usize)->Option<[Vec3;3]> {
    let tri=source.raw(id);
    ((tri[1]-tri[0]).cross(tri[2]-tri[0]).length_squared()>1e-16)
        .then(||tri.map(|p|Vec3::new(p.x,-p.z,p.y)/0.0254))
}
fn spatial(p:Vec3)->[i32;3] {p.to_array().map(|x|(x/CELL).floor() as i32)}
fn owner_box(p:Vec3,anchor:[f64;3])->crate::compact_world::Bounds {
    let p=p.to_array();let scale=p.into_iter().map(f32::abs).fold(1.,f32::max);
    let pad=0.5+f64::from(scale*f32::EPSILON*32.)*2.54;
    crate::compact_world::Bounds {
        min:std::array::from_fn(|i|f64::from(p[i])*2.54+anchor[i]-pad),
        max:std::array::from_fn(|i|f64::from(p[i])*2.54+anchor[i]+pad),
    }
}
fn overlap(a:crate::compact_world::Bounds,b:crate::compact_world::Bounds)->bool {
    (0..3).all(|i|a.min[i]<=b.max[i]&&b.min[i]<=a.max[i])
}
/// Share a broad query among nearby endpoint queries, then apply each exact
/// original endpoint box. Canonical ID order still chooses the first owner.
/// Sorting affects only traversal; results return in their input order.
fn canonical_owners(source:&CompactGeometry,requests:&[(EdgeKey,Vec3)])
    ->Vec<Option<EdgeCandidate>> {
    let started=Instant::now();let mut order:Vec<_>=(0..requests.len()).collect();
    order.sort_unstable_by_key(|&i|spatial(requests[i].1));
    let mut result=vec![None;requests.len()];let mut start=0;let mut queries=0;
    while start<order.len() {
        let cell=spatial(requests[order[start]].1);let mut end=start+1;
        while end<order.len()&&spatial(requests[order[end]].1)==cell {end+=1;}
        let mut query=crate::compact_world::Bounds{min:[f64::INFINITY;3],max:[f64::NEG_INFINITY;3]};
        let mut pending=HashMap::new();
        for &i in &order[start..end] {
            let bound=owner_box(requests[i].1,source.anchor);
            for axis in 0..3 {query.min[axis]=query.min[axis].min(bound.min[axis]);query.max[axis]=query.max[axis].max(bound.max[axis]);}
            pending.insert(requests[i].0,(i,bound));
        }
        for id in source.world.candidate_triangles(query.min,query.max) {
            let Some(tri)=valid_triangle(source,id) else {continue};
            let raw=source.world.raw_triangle(id);
            let bound=crate::compact_world::Bounds {
                min:std::array::from_fn(|axis|raw.iter().map(|p|p[axis]).fold(f64::INFINITY,f64::min)),
                max:std::array::from_fn(|axis|raw.iter().map(|p|p[axis]).fold(f64::NEG_INFINITY,f64::max)),
            };
            for (key,edge) in example_rails::face_candidates(id,tri) {
                if let Some(&(i,endpoint))=pending.get(&key) {
                    if overlap(bound,endpoint) {result[i]=Some(edge);pending.remove(&key);}
                }
            }
            if pending.is_empty(){break;}
        }
        queries+=1;start=end;
        if queries%100_000==0 {eprintln!("WORLD_RAIL_OWNERS edges={} total={} queries={queries} elapsed_ms={}",start,requests.len(),started.elapsed().as_millis());}
    }
    eprintln!("WORLD_RAIL_OWNERS_DONE edges={} queries={queries} elapsed_ms={}",requests.len(),started.elapsed().as_millis());result
}
fn covered_owners(source:&CompactGeometry,requests:&[(EdgeKey,Vec3)],
    known:&HashMap<EdgeKey,usize>,regions:&crate::compact_world::BoundsIndex)->Vec<Option<EdgeCandidate>> {
    let started=Instant::now();let mut result=vec![None;requests.len()];let mut boundary=Vec::new();let mut indices=Vec::new();
    for (i,&(key,a)) in requests.iter().enumerate() {
        let query=owner_box(a,source.anchor);
        if regions.contains_box(query) {
            // Every candidate in the original endpoint query also intersects
            // this original changed box, so it was already collected. Its
            // minimum global ordinal is the exact canonical owner.
            match known.get(&key) {
                None=>continue,
                Some(&id)=>{
                    let raw=source.world.raw_triangle(id);
                    let bounds=crate::compact_world::Bounds {
                        min:std::array::from_fn(|axis|raw.iter().map(|p|p[axis]).fold(f64::INFINITY,f64::min)),
                        max:std::array::from_fn(|axis|raw.iter().map(|p|p[axis]).fold(f64::NEG_INFINITY,f64::max)),
                    };
                    if overlap(bounds,query) {
                        result[i]=valid_triangle(source,id).and_then(|tri|example_rails::face_candidates(id,tri).into_iter().find_map(|(k,e)|(k==key).then_some(e)));
                        if result[i].is_some(){continue;}
                    }
                }
            }
        }
        indices.push(i);boundary.push((key,a));
    }
    eprintln!("WORLD_RAIL_OWNERS_COVERED total={} boundary={} elapsed_ms={}",requests.len(),boundary.len(),started.elapsed().as_millis());
    for (i,owner) in indices.into_iter().zip(canonical_owners(source,&boundary)){result[i]=owner;}result
}
#[cfg(test)]
pub fn verify_owner_batching(source:&CompactGeometry) {
    let mut requests=HashMap::new();
    for group in source.world.groups(){for id in group.triangle_range.clone(){
        if let Some(triangle)=valid_triangle(source,id){for (key,edge) in example_rails::face_candidates(id,triangle){requests.entry(key).or_insert(edge.a);}}
    }}
    let mut requests:Vec<_>=requests.into_iter().collect();
    // Also exercise absent endpoints and boxes on either side of cell borders.
    for value in [-128.0001,-128.,-127.9999,-0.0001,0.,0.0001,63.9999,64.,64.0001] {
        let a=Vec3::new(value,value,1000.);let b=a+Vec3::X*32.;requests.push((example_rails::edge_key(a,b),a));
    }
    let actual=canonical_owners(source,&requests);let mut reference=Vec::new();
    for (&(key,a),actual) in requests.iter().zip(actual) {
        let bound=owner_box(a,source.anchor);let mut expected=None;
        'owner:for id in source.world.candidate_triangles(bound.min,bound.max) {
            if let Some(tri)=valid_triangle(source,id){for (candidate,edge) in example_rails::face_candidates(id,tri){
                if candidate==key{expected=Some(edge);break 'owner;}
            }}
        }
        assert_eq!(actual.map(|e|(e.owner,encoded(&e.into()))),expected.map(|e|(e.owner,encoded(&e.into()))));
        reference.push(expected);
    }
    use crate::compact_world::{Bounds,BoundsIndex};
    let bounds=source.world.groups().iter().map(|g|g.bounds).collect::<Vec<_>>();
    for regions in [vec![Bounds{min:[-1e9;3],max:[1e9;3]}],
        vec![Bounds{min:[-500.,-200.,-200.],max:[1000.,200.,200.]}],bounds,
        requests.iter().step_by(7).map(|(_,a)|owner_box(*a,source.anchor)).collect()] {
        let mut known=HashMap::<EdgeKey,usize>::new();
        for id in source.world.candidate_triangles_many(&regions) {
            if let Some(tri)=valid_triangle(source,id){for (key,_) in example_rails::face_candidates(id,tri){known.entry(key).and_modify(|old|*old=(*old).min(id)).or_insert(id);}}
        }
        let actual=covered_owners(source,&requests,&known,&BoundsIndex::new(&regions));
        for (a,b) in actual.into_iter().zip(&reference) {
            assert_eq!(a.map(|e|(e.owner,encoded(&e.into()))),b.map(|e|(e.owner,encoded(&e.into()))));
        }
    }
}
struct Probe<'a> {source:&'a CompactGeometry,cells:HashMap<[i32;3],Vec<[Vec3;3]>>,fifo:VecDeque<[i32;3]>}
impl<'a> Probe<'a> {
    fn new(source:&'a CompactGeometry)->Self {Self{source,cells:HashMap::new(),fifo:VecDeque::new()}}
    fn hit(&mut self,origin:Vec3,dir:Vec3,len:f32)->bool {
        let key=spatial(origin);
        if !self.cells.contains_key(&key) {
            // Every original lip ray is at most four inches long. This cell
            // halo covers every ray from the cell; extra faces only reach the
            // unchanged exact predicate and cannot change a hit result.
            let lo=key.map(|x|f64::from(x)*f64::from(CELL)*2.54-16.);
            let hi=key.map(|x|f64::from(x+1)*f64::from(CELL)*2.54+16.);
            let scale=lo.into_iter().chain(hi).map(f64::abs).fold(1.,f64::max);
            let pad=scale*f64::from(f32::EPSILON)*32.+0.001;
            let lo=std::array::from_fn(|i|lo[i]+self.source.anchor[i]-pad);
            let hi=std::array::from_fn(|i|hi[i]+self.source.anchor[i]+pad);
            let triangles=self.source.world.candidate_triangles(lo,hi).into_iter().filter_map(|id|valid_triangle(self.source,id)).collect();
            while self.cells.len()>=128 {if let Some(old)=self.fifo.pop_front(){self.cells.remove(&old);}}
            self.cells.insert(key,triangles);self.fifo.push_back(key);
        }
        self.cells[&key].iter().any(|tri|example_rails::ray_triangle(origin,dir,len,tri))
    }
}
fn evaluate(source:&CompactGeometry,edges:HashMap<EdgeKey,EdgeCandidate>,maximum_threads:usize,terrain_seed:bool)->Vec<EdgeCandidate> {
    let total=edges.len();let started=Instant::now();
    // Probes are independent boolean queries. Spatially batch them, then
    // restore their original HashMap iteration order before merge/chain.
    let mut edges:Vec<_>=edges.into_values().enumerate().collect();
    edges.sort_unstable_by_key(|(_,e)|spatial((e.a+e.b)*0.5));
    let threads=std::thread::available_parallelism().map_or(1,usize::from).min(maximum_threads)
        .min((total/100_000).max(1));
    let mut lips=std::thread::scope(|scope|{
        let jobs:Vec<_>=edges.chunks(total.div_ceil(threads).max(1)).enumerate().map(|(worker,chunk)|scope.spawn(move||{
            let mut probe=Probe::new(source);let mut lips=Vec::new();
            for (i,&(ordinal,edge)) in chunk.iter().enumerate(){
                // Continuous terrain usually rejects a lip on the adjoining
                // quad. Test those actual world faces first; every miss still
                // reaches the global source query, including component seams,
                // holes and objects. Only boolean predicate order changes.
                let neighbors=if terrain_seed {source.world.terrain_neighbors(edge.owner)} else {Vec::new()};
                let seed:Vec<_>=neighbors.into_iter()
                    .filter_map(|id|valid_triangle(source,id)).collect();
                if example_rails::candidate_is_lip(edge,|o,d,l|
                    seed.iter().any(|t|example_rails::ray_triangle(o,d,l,t))||probe.hit(o,d,l)) {lips.push((ordinal,edge));}
                if (i+1)%1_000_000==0 {eprintln!("WORLD_RAIL_PROBES worker={worker} done={} total={} lips={} elapsed_ms={}",i+1,chunk.len(),lips.len(),started.elapsed().as_millis());}
            }lips
        })).collect();
        jobs.into_iter().flat_map(|job|job.join().expect("Original rail probe worker panicked")).collect::<Vec<_>>()
    });
    lips.sort_unstable_by_key(|(ordinal,_)|*ordinal);lips.into_iter().map(|(_,e)|e).collect()
}
pub fn bake(source:&CompactGeometry)->Result<LipBake,String> {
    if source.anchor!=[0.;3] {return Err("Permanent rails require the manifest's fixed UE origin".into());}
    let started=Instant::now();let mut edges=HashMap::new();let mut done=0usize;
    for group in source.world.groups() {for id in group.triangle_range.clone() {
        if let Some(tri)=valid_triangle(source,id) {for (key,edge) in example_rails::face_candidates(id,tri) {edges.entry(key).or_insert(edge);}}
        done+=1;
        if done%1_000_000==0 {eprintln!("WORLD_RAIL_CANDIDATES triangles={done} edges={} elapsed_ms={}",edges.len(),started.elapsed().as_millis());}
    }}
    let candidates=edges.len();let lips=evaluate(source,edges,4,true).into_iter().map(Lip::from).collect::<Vec<_>>();
    eprintln!("WORLD_RAIL_BAKE candidates={candidates} lips={} elapsed_ms={}",lips.len(),started.elapsed().as_millis());
    Ok(LipBake{fingerprint:fingerprint(&source.world).into(),candidates,lips})
}
fn encoded(edge:&Lip)->[u8;RECORD] {
    let mut data=[0;RECORD];
    for (i,value) in [edge.a,edge.b,edge.normal,edge.centroid].into_iter().flat_map(|p|p.to_array()).enumerate(){data[i*4..4+i*4].copy_from_slice(&value.to_le_bytes());}data
}
pub fn save(path:&Path,bake:&LipBake)->Result<(),String> {
    save_with_identity(path,bake,&bake.fingerprint)
}
pub fn payload_digest(bake:&LipBake)->blake3::Hash {
    let mut hash=blake3::Hasher::new();hash.update(&(bake.candidates as u64).to_le_bytes());hash.update(&(bake.lips.len() as u64).to_le_bytes());
    for edge in &bake.lips {hash.update(&encoded(edge));}hash.finalize()
}
pub fn save_with_identity(path:&Path,bake:&LipBake,identity:&str)->Result<(),String> {
    if identity.len()!=64||!identity.bytes().all(|b|b.is_ascii_hexdigit()) {return Err("Invalid permanent rail fingerprint".into());}
    let mut header=Vec::with_capacity(HEADER);header.extend(b"S3L1");header.extend(VERSION.to_le_bytes());
    header.extend((bake.lips.len() as u64).to_le_bytes());header.extend((bake.candidates as u64).to_le_bytes());
    header.extend(identity.as_bytes());
    let mut hash=blake3::Hasher::new();hash.update(&header);for edge in &bake.lips {hash.update(&encoded(edge));}
    let temporary=path.with_extension(format!("{}-{}.tmp",std::process::id(),bake.lips.len()));
    let mut file=std::io::BufWriter::new(std::fs::File::create(&temporary).map_err(|e|e.to_string())?);
    header.extend(hash.finalize().as_bytes());
    file.write_all(&header).map_err(|e|e.to_string())?;
    for edge in &bake.lips {file.write_all(&encoded(edge)).map_err(|e|e.to_string())?;}
    file.flush().map_err(|e|e.to_string())?;file.get_ref().sync_all().map_err(|e|e.to_string())?;drop(file);
    std::fs::rename(&temporary,path).map_err(|e|e.to_string())
}
pub fn load(path:&Path,world:&CompactWorld)->Result<LipBake,String> {
    // Existing permanent v2 bakes have a payload checksum. Preserve their
    // compatibility; every new write and all scene-cache hits require v3.
    load_identity(path,world,fingerprint(world),true)
}
pub fn load_with_identity(path:&Path,world:&CompactWorld,identity:&str)->Result<LipBake,String> {
    load_identity(path,world,identity,false)
}
fn load_identity(path:&Path,world:&CompactWorld,identity:&str,allow_legacy:bool)->Result<LipBake,String> {
    let mut file=std::io::BufReader::new(std::fs::File::open(path).map_err(|e|e.to_string())?);
    let mut h=[0;HEADER];file.read_exact(&mut h).map_err(|e|e.to_string())?;
    let version=u32::from_le_bytes(h[4..8].try_into().unwrap());
    if &h[..4]!=b"S3L1"||!(version==VERSION||(allow_legacy&&version==2))||h[24..88]!=*identity.as_bytes(){return Err("Permanent rail bake belongs to a different world/version".into());}
    let count=usize::try_from(u64::from_le_bytes(h[8..16].try_into().unwrap())).map_err(|_|"Lip count overflow")?;
    let candidates=usize::try_from(u64::from_le_bytes(h[16..24].try_into().unwrap())).map_err(|_|"Candidate count overflow")?;
    let limit=world.triangle_count().checked_mul(3).ok_or("World edge count overflow")?;
    let bytes=count.checked_mul(RECORD).and_then(|n|n.checked_add(HEADER)).ok_or("Lip file size overflow")?;
    if count>candidates||candidates>limit||file.get_ref().metadata().map_err(|e|e.to_string())?.len()!=bytes as u64{return Err("Invalid permanent lip count/size".into());}
    let mut lips=Vec::with_capacity(count);let mut hash=blake3::Hasher::new();
    if version>=3 {hash.update(&h[..88]);}
    for _ in 0..count {
        let mut b=[0;RECORD];file.read_exact(&mut b).map_err(|e|e.to_string())?;hash.update(&b);
        let points:[Vec3;4]=std::array::from_fn(|i|Vec3::from_array(std::array::from_fn(|j|{let o=(i*3+j)*4;f32::from_le_bytes(b[o..o+4].try_into().unwrap())})));
        if !points.iter().all(|p|p.is_finite()){return Err("Invalid permanent lip data".into());}
        lips.push(Lip{a:points[0],b:points[1],normal:points[2],centroid:points[3]});
    }
    if hash.finalize().as_bytes()!=&h[88..120] {return Err("Permanent lip checksum mismatch".into());}
    Ok(LipBake{fingerprint:fingerprint(world).into(),candidates,lips})
}
pub fn rails(bake:&LipBake)->Vec<Vec<[f32;3]>> {
    let (rails,census)=example_rails::finish_lips(bake.lips.iter().map(|e|(e.a,e.b)).collect(),bake.candidates);
    eprintln!("WORLD_RAIL_READY candidates={} lips={} runs={} rails={}",census.candidates,census.lips,census.runs,census.rails);
    rails.into_iter().map(|rail|rail.into_iter().map(|p|[p.x*0.0254,p.z*0.0254,-p.y*0.0254]).collect()).collect()
}

/// Re-probe every candidate that a changed object can cover or expose. Global
/// edge ownership is resolved against the current complete source, including
/// coincident faces outside an object's package. Merge/chain still runs once
/// over all surviving lips, so rails can cross package and instance seams.
pub fn overlay(base:&LipBake,source:&CompactGeometry)->Result<LipBake,String> {
    if source.anchor!=[0.;3] {return Err("Permanent rail overlays require the manifest's fixed UE origin".into());}
    let pristine=source.world.pristine_base();
    if base.fingerprint!=pristine.source_fingerprint {return Err("Overlay grind bake base mismatch".into());}
    let base_source=CompactGeometry::new(Arc::clone(&pristine),source.anchor)?;
    update_bounded(base,&base_source,source,source.world.changed_bounds(),4)
}
/// The same global candidate rules applied only where accepted snapshots differ.
pub fn update(previous:&LipBake,previous_source:&CompactGeometry,source:&CompactGeometry,
    changed_bounds:Vec<crate::compact_world::Bounds>)->Result<LipBake,String> {
    update_bounded(previous,previous_source,source,changed_bounds,2)
}
fn update_bounded(previous:&LipBake,previous_source:&CompactGeometry,source:&CompactGeometry,
    changed_bounds:Vec<crate::compact_world::Bounds>,maximum_threads:usize)->Result<LipBake,String> {
    if source.anchor!=[0.;3]||previous_source.anchor!=[0.;3] {return Err("Permanent lip updates require the fixed UE origin".into());}
    if previous.fingerprint!=fingerprint(&previous_source.world)
        ||previous_source.world.source_fingerprint!=source.world.source_fingerprint {return Err("Lip update snapshot mismatch".into());}
    let started=Instant::now();let bounds_count=changed_bounds.len();
    eprintln!("WORLD_RAIL_UPDATE_BEGIN bounds={bounds_count}");
    let mut touched=HashMap::<EdgeKey,(Vec3,Vec3)>::new();
    let regions:Vec<_>=changed_bounds.into_iter().map(|bounds| {
        let scale=bounds.min.into_iter().chain(bounds.max).map(f64::abs).fold(1.,f64::max);
        let pad=16.+scale*f64::from(f32::EPSILON)*32.;
        let lo=bounds.min.map(|v|v-pad);let hi=bounds.max.map(|v|v+pad);
        crate::compact_world::Bounds{min:lo,max:hi}
    }).collect();
    let region_index=crate::compact_world::BoundsIndex::new(&regions);
    let ids=source.world.candidate_triangles_many(&regions);let old_ids=previous_source.world.candidate_triangles_many(&regions);
    eprintln!("WORLD_RAIL_UPDATE_BOUNDS_DONE new_triangles={} old_triangles={} elapsed_ms={}",ids.len(),old_ids.len(),started.elapsed().as_millis());
    let mut old_keys=HashSet::new();let mut new_keys=HashMap::<EdgeKey,usize>::new();
    for id in old_ids {if let Some(tri)=valid_triangle(previous_source,id) {for (key,edge) in example_rails::face_candidates(id,tri){old_keys.insert(key);touched.entry(key).or_insert((edge.a,edge.b));}}}
    for id in ids {if let Some(tri)=valid_triangle(source,id) {for (key,edge) in example_rails::face_candidates(id,tri){
        new_keys.entry(key).and_modify(|owner|*owner=(*owner).min(id)).or_insert(id);touched.entry(key).or_insert((edge.a,edge.b));
    }}}
    eprintln!("WORLD_RAIL_UPDATE_KEYS old={} new={} touched={} elapsed_ms={}",old_keys.len(),new_keys.len(),touched.len(),started.elapsed().as_millis());
    let requests:Vec<_>=touched.iter().map(|(&key,&(a,_))|(key,a)).collect();
    let owners=covered_owners(source,&requests,&new_keys,&region_index);let mut current=HashMap::new();
    for ((key,_),owner) in requests.into_iter().zip(owners) {if let Some(edge)=owner {current.insert(key,edge);}}
    if current.len()!=new_keys.len()||current.keys().any(|key|!new_keys.contains_key(key)){return Err("Lip update did not cover every changed candidate".into());}
    let candidates=previous.candidates.checked_sub(old_keys.len()).and_then(|n|n.checked_add(new_keys.len())).ok_or("Lip candidate census overflow")?;
    // Endpoint representatives are no longer needed. Keep the already built
    // old/new key sets for union membership instead of retaining another large
    // edge table during original ray probing.
    drop(touched);drop(region_index);
    let reevaluated=evaluate(source,current,maximum_threads,true).into_iter().map(Lip::from).collect::<Vec<_>>();
    let positions:HashMap<_,_>=reevaluated.iter().enumerate().map(|(i,e)|(example_rails::edge_key(e.a,e.b),i)).collect();
    let mut used=vec![false;reevaluated.len()];let mut lips=Vec::new();
    for edge in &previous.lips {
        let key=example_rails::edge_key(edge.a,edge.b);
        if !old_keys.contains(&key)&&!new_keys.contains_key(&key){lips.push(*edge);}
        else if let Some(&i)=positions.get(&key){lips.push(reevaluated[i]);used[i]=true;}
    }
    // A surviving base lip retains its position, including a no-op observed
    // package replacement. Only newly eligible keys append to the input list.
    lips.extend(reevaluated.into_iter().enumerate().filter_map(|(i,e)|(!used[i]).then_some(e)));
    eprintln!("WORLD_RAIL_UPDATE_DONE candidates={candidates} lips={} elapsed_ms={}",lips.len(),started.elapsed().as_millis());
    Ok(LipBake{fingerprint:fingerprint(&source.world).into(),candidates,lips})
}

#[cfg(test)]
pub fn benchmark_terrain_seed(source:&CompactGeometry,limit:usize) {
    let mut edges=HashMap::new();let mut count=0;
    'groups:for group in source.world.groups(){for id in group.triangle_range.clone(){
        if source.world.terrain_neighbors(id).is_empty(){continue;}
        if let Some(tri)=valid_triangle(source,id){for (key,edge) in example_rails::face_candidates(id,tri){edges.entry(key).or_insert(edge);}}
        count+=1;if count>=limit {break 'groups;}
    }}
    let start=Instant::now();let reference=evaluate(source,edges.clone(),1,false);let original=start.elapsed();
    let start=Instant::now();let seeded=evaluate(source,edges,1,true);let optimized=start.elapsed();
    let records=|values:Vec<EdgeCandidate>|values.into_iter().map(|e|encoded(&e.into())).collect::<Vec<_>>();
    assert_eq!(records(seeded),records(reference));
    eprintln!("WORLD_RAIL_TERRAIN_SEED triangles={count} global_ms={:.3} seeded_ms={:.3}",original.as_secs_f64()*1000.,optimized.as_secs_f64()*1000.);
}
