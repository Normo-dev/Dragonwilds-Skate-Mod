//! Immutable S3W1 owned-world storage. No anchor, solver, or live IPC dependency.
use serde::Deserialize;
use sha2::{Digest,Sha256};
use std::{collections::{HashMap,HashSet},fs, ops::Range, path::{Component,Path},sync::Arc};

#[derive(Clone,Copy,Debug,PartialEq)]
pub struct Bounds { pub min:[f64;3], pub max:[f64;3] }
impl Bounds {
    fn valid(self)->bool { (0..3).all(|i|self.min[i].is_finite()&&self.max[i].is_finite()&&self.min[i]<=self.max[i]) }
    fn overlaps(self,b:Self)->bool { (0..3).all(|i|self.min[i]<=b.max[i]&&b.min[i]<=self.max[i]) }
    fn contains(self,b:Self)->bool { (0..3).all(|i|self.min[i]<=b.min[i]&&b.max[i]<=self.max[i]) }
    fn union(self,b:Self)->Self { Self{min:std::array::from_fn(|i|self.min[i].min(b.min[i])),max:std::array::from_fn(|i|self.max[i].max(b.max[i]))} }
    fn points(points:impl IntoIterator<Item=[f64;3]>)->Self {
        points.into_iter().fold(Self{min:[f64::INFINITY;3],max:[f64::NEG_INFINITY;3]},|b,p|
            b.union(Self{min:p,max:p}))
    }
}

struct Node { bounds:Bounds, children:Option<[usize;2]>, range:Range<usize> }
struct Bvh { nodes:Vec<Node>, order:Vec<usize> }
impl Bvh {
    fn new(bounds:&[Bounds])->Self {
        let mut result=Self{nodes:Vec::new(),order:(0..bounds.len()).collect()};
        if !bounds.is_empty(){result.build(bounds,0..bounds.len());}result
    }
    fn build(&mut self,bounds:&[Bounds],range:Range<usize>)->usize {
        let bound=self.order[range.clone()].iter().map(|&i|bounds[i]).reduce(Bounds::union).unwrap();
        let index=self.nodes.len();self.nodes.push(Node{bounds:bound,children:None,range:range.clone()});
        if range.len()>8 {
            let axis=(0..3).max_by(|&a,&b|(bound.max[a]-bound.min[a]).total_cmp(&(bound.max[b]-bound.min[b]))).unwrap();
            let middle=range.start+range.len()/2;
            self.order[range.clone()].select_nth_unstable_by(range.len()/2,|&a,&b|
                (bounds[a].min[axis]+bounds[a].max[axis]).total_cmp(&(bounds[b].min[axis]+bounds[b].max[axis])).then(a.cmp(&b)));
            let a=self.build(bounds,range.start..middle);let b=self.build(bounds,middle..range.end);
            self.nodes[index].children=Some([a,b]);
        } index
    }
    fn query(&self,query:Bounds)->Vec<usize> {
        let mut out=Vec::new();if self.nodes.is_empty(){return out;}
        let mut stack=vec![0];while let Some(i)=stack.pop(){let n=&self.nodes[i];if !n.bounds.overlaps(query){continue;}
            if let Some([a,b])=n.children{stack.push(b);stack.push(a);}else{out.extend_from_slice(&self.order[n.range.clone()]);}}
        out
    }
    fn query_transformed(&self,query:Bounds,r:[[f64;3];3],v:[f64;3])->Vec<usize> {
        let mut out=Vec::new();if self.nodes.is_empty(){return out;}
        let mut stack=vec![0];while let Some(i)=stack.pop(){let n=&self.nodes[i];
            let world=transformed_bounds(r,v,n.bounds);
            let pad=world.min.iter().chain(world.max.iter()).fold(1f64,|a,v|a.max(v.abs()))*f64::EPSILON*64.;
            if !(Bounds{min:world.min.map(|v|v-pad),max:world.max.map(|v|v+pad)}).overlaps(query){continue;}
            if let Some([a,b])=n.children{stack.push(b);stack.push(a);}else{out.extend_from_slice(&self.order[n.range.clone()]);}}
        out
    }
    /// Visit exact query-box leaves without allocating a vector for each face.
    fn any_overlap(&self,query:Bounds,leaves:&[Bounds])->bool {
        fn visit(tree:&Bvh,index:usize,query:Bounds,leaves:&[Bounds])->bool {
            let node=&tree.nodes[index];if !node.bounds.overlaps(query){return false;}
            if let Some([a,b])=node.children{return visit(tree,a,query,leaves)||visit(tree,b,query,leaves);}
            tree.order[node.range.clone()].iter().any(|&i|leaves[i].overlaps(query))
        }
        !self.nodes.is_empty()&&visit(self,0,query,leaves)
    }
    fn any_contains(&self,query:Bounds,leaves:&[Bounds])->bool {
        fn visit(tree:&Bvh,index:usize,query:Bounds,leaves:&[Bounds])->bool {
            let node=&tree.nodes[index];if !node.bounds.contains(query){return false;}
            if let Some([a,b])=node.children{return visit(tree,a,query,leaves)||visit(tree,b,query,leaves);}
            tree.order[node.range.clone()].iter().any(|&i|leaves[i].contains(query))
        }
        !self.nodes.is_empty()&&visit(self,0,query,leaves)
    }
    /// Return whether one exact leaf covers the group, otherwise the union of
    /// overlapping query leaves. The union is conservative; a later exact test
    /// rejects gaps between the original boxes.
    fn coverage(&self,group:Bounds,proof:Bounds,leaves:&[Bounds])->(bool,Option<Bounds>) {
        fn visit(tree:&Bvh,index:usize,group:Bounds,proof:Bounds,leaves:&[Bounds],union:&mut Option<Bounds>)->bool {
            let node=&tree.nodes[index];if !node.bounds.overlaps(group){return false;}
            if let Some([a,b])=node.children{return visit(tree,a,group,proof,leaves,union)||visit(tree,b,group,proof,leaves,union);}
            for &i in &tree.order[node.range.clone()] {
                let bound=leaves[i];if !bound.overlaps(group){continue;}
                if bound.contains(proof){return true;}
                *union=Some(union.map_or(bound,|old|old.union(bound)));
            }false
        }
        let mut union=None;let full=!self.nodes.is_empty()&&visit(self,0,group,proof,leaves,&mut union);(full,union)
    }
}

/// Reusable index of exact finite query boxes. Containment requires one actual
/// input box; coverage by only a node union never establishes that proof.
pub struct BoundsIndex { boxes:Vec<Bounds>,tree:Bvh }
impl BoundsIndex {
    pub fn new(bounds:&[Bounds])->Self {
        let boxes:Vec<_>=bounds.iter().copied().filter(|b|b.valid()).collect();
        let tree=Bvh::new(&boxes);Self{boxes,tree}
    }
    pub fn contains_box(&self,query:Bounds)->bool {
        query.valid()&&self.tree.any_contains(query,&self.boxes)
    }
}

#[derive(Clone,Debug)]
pub struct WorldGroup { pub triangle_range:Range<usize>,pub bounds:Bounds }
#[derive(Clone,Debug)]
pub struct WorldCounts { pub geometry_triangles:usize,pub instances:usize,pub terrain_components:usize,
    pub terrain_triangles:usize,pub static_triangles:usize,pub binary_bytes:u64 }
#[derive(Deserialize)]
struct FileRecord { path:String,bytes:u64,sha256:String }
#[derive(Deserialize)]
struct Evidence { reason:String,trace:Option<u32> }
#[derive(Deserialize)]
struct GeometryRecord { id:String,offset:usize,count:usize,unsupported:usize,empty_evidence:Option<Evidence> }
#[derive(Deserialize)]
struct TerrainRecord { n:usize,mirrored:bool,min:[f64;3],max:[f64;3],points:FileRecord,visibility:FileRecord,
    triangle_start:usize,triangle_count:usize }
#[derive(Deserialize)]
struct Completeness { complete:bool,missing_geometry_count:usize,unsupported_meshes:usize,unresolved_empty_meshes:usize }
#[derive(Deserialize)]
struct Manifest { magic:String,schema:u32,coordinate_system:String,byte_order:String,source_fingerprint:String,
    default_shape_complexity:u32,completeness:Completeness,geometry:Vec<GeometryRecord>,geometry_file:FileRecord,
    geometry_triangle_count:usize,packages:Vec<String>,instance_file:FileRecord,instance_count:usize,instance_stride:usize,
    terrain:Vec<TerrainRecord>,terrain_triangle_count:usize,static_triangle_count:usize,visibility_hole_threshold:u8,binary_bytes:u64 }
#[derive(Deserialize)]
struct OverlayManifest { magic:String,schema:u32,base_fingerprint:String,source_fingerprint:String,packages:Vec<String>,
    instance_packages:Vec<String>,geometry_base_count:usize,geometry:Vec<GeometryRecord>,geometry_file:FileRecord,
    instance_file:FileRecord,instance_count:usize,instance_stride:usize,triangle_count:usize,default_shape_complexity:u32,
    completeness:Completeness,binary_bytes:u64 }
struct Mesh { range:Range<usize>,bounds:Bounds,tree:Bvh,digest:[u8;32] }
struct Instance { geometry:usize,package:usize,r:[[f64;3];3],v:[f64;3],mirrored:bool }
struct TerrainTile { rows:Range<usize>,columns:Range<usize>,bounds:Bounds }
struct Terrain { n:usize,mirrored:bool,points:Vec<[f32;3]>,visible:Vec<u32>,tiles:Vec<TerrainTile>,tree:Bvh }
enum Source { Terrain(usize),Instance(usize),Base(usize) }
pub struct CompactWorld { pub source_fingerprint:String,pub counts:WorldCounts,
    pub scene_fingerprint:Option<String>,base:Option<Arc<CompactWorld>>,packages:Vec<String>,default_policy:u32,
    changes:Vec<Bounds>,
    package_content:HashMap<String,(bool,[u8;32])>,
    package_sequence:Vec<String>,
    triangles:Vec<[[f64;3];3]>,meshes:Vec<Mesh>,instances:Vec<Instance>,terrain:Vec<Terrain>,
    groups:Vec<WorldGroup>,sources:Vec<Source>,tree:Bvh,total:usize }

fn read_file(root:&Path,record:&FileRecord)->Result<Vec<u8>,String> {
    if record.path.is_empty() || Path::new(&record.path).components().any(|c|!matches!(c,Component::Normal(_))){
        return Err("Invalid S3W1 relative file path".into());}
    let path=root.join(&record.path).canonicalize().map_err(|e|e.to_string())?;
    if !path.starts_with(root){return Err("S3W1 file escapes cache directory".into());}
    let length=fs::metadata(&path).map_err(|e|e.to_string())?.len();
    if length!=record.bytes||length>2_000_000_000{return Err(format!("S3W1 file length invalid: {}",record.path));}
    let bytes=fs::read(&path).map_err(|e|e.to_string())?;
    let digest:String=Sha256::digest(&bytes).iter().map(|b|format!("{b:02x}")).collect();
    if digest!=record.sha256{return Err(format!("S3W1 checksum mismatch: {}",record.path));}
    Ok(bytes)
}
fn f64_at(bytes:&[u8],at:usize)->f64 { f64::from_le_bytes(bytes[at..at+8].try_into().unwrap()) }
fn vector(bytes:&[u8],at:usize)->[f64;3] { std::array::from_fn(|i|f64_at(bytes,at+i*8)) }
fn transform(r:[[f64;3];3],v:[f64;3],p:[f64;3])->[f64;3] {
    std::array::from_fn(|i|((r[i][0]*p[0]+r[i][1]*p[1])+r[i][2]*p[2])+v[i])
}
fn determinant(r:[[f64;3];3])->f64 {
    let [[a,b,c],[d,e,f],[g,h,i]]=r;a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
}
fn transformed_bounds(r:[[f64;3];3],v:[f64;3],bounds:Bounds)->Bounds {
    Bounds::points((0..8).map(|corner|transform(r,v,std::array::from_fn(|i|if corner&(1<<i)==0{bounds.min[i]}else{bounds.max[i]}))))
}
fn mesh_digest(triangles:&[[[f64;3];3]])->[u8;32]{
    let mut hash=Sha256::new();for value in triangles.iter().flatten().flatten(){hash.update(value.to_le_bytes());}hash.finalize().into()
}
fn hash_instance(hash:&mut Sha256,mesh:&Mesh,instance:&Instance){
    hash.update(mesh.digest);
    for value in instance.r.iter().flatten().chain(instance.v.iter()){hash.update(value.to_le_bytes());}
}
fn package_sequence<'a>(packages:impl IntoIterator<Item=&'a str>)->Vec<String>{
    let mut result=Vec::<String>::new();
    for package in packages{if result.last().is_none_or(|last|last!=package){result.push(package.to_owned());}}
    result
}

impl CompactWorld {
    pub fn load(manifest:&Path)->Result<Self,String> {
        let root=manifest.parent().ok_or("S3W1 manifest has no directory")?.canonicalize().map_err(|e|e.to_string())?;
        let bytes=fs::read(manifest).map_err(|e|e.to_string())?;if bytes.len()>32*1024*1024{return Err("S3W1 manifest too large".into());}
        let m:Manifest=serde_json::from_slice(&bytes).map_err(|e|e.to_string())?;
        if m.magic!="S3W1"||m.schema!=1||m.coordinate_system!="Unreal centimetres XYZ"||m.byte_order!="little"
            ||m.visibility_hole_threshold!=170||m.instance_stride!=152||!m.completeness.complete
            ||m.completeness.missing_geometry_count!=0||m.completeness.unsupported_meshes!=0||m.completeness.unresolved_empty_meshes!=0
            ||!(1..=3).contains(&m.default_shape_complexity)||m.source_fingerprint.len()!=64 {
            return Err("Unsupported/incomplete S3W1 manifest".into());}
        let bytes=read_file(&root,&m.geometry_file)?;
        if m.geometry_triangle_count.checked_mul(72)!=Some(bytes.len()){return Err("S3W1 geometry count mismatch".into());}
        let triangles:Vec<_>=bytes.chunks_exact(72).map(|b|[vector(b,0),vector(b,24),vector(b,48)]).collect();drop(bytes);
        if !triangles.iter().flatten().flatten().all(|v|v.is_finite()){return Err("Non-finite S3W1 geometry".into());}
        let mut meshes=Vec::new();let mut offset=0;
        for record in &m.geometry {
            if record.offset!=offset||record.unsupported!=0{return Err(format!("Invalid S3W1 geometry {}",record.id));}
            let end=offset.checked_add(record.count).filter(|&n|n<=triangles.len()).ok_or("S3W1 geometry range overflow")?;
            if record.count==0 {
                let evidence=record.empty_evidence.as_ref().ok_or("Unresolved S3W1 empty geometry")?;
                let effective=if evidence.trace==Some(0){Some(m.default_shape_complexity)}else{evidence.trace};
                if evidence.reason!="no_cooked_physics_shapes" && !(["complex_only","all_simple_shapes_disabled"].contains(&evidence.reason.as_str())
                    && matches!(effective,Some(1|2))){return Err("S3W1 empty evidence does not match collision policy".into());}
            }
            let bounds:Vec<_>=triangles[offset..end].iter().map(|t|Bounds::points(*t)).collect();let tree=Bvh::new(&bounds);
            let bounds=bounds.into_iter().reduce(Bounds::union).unwrap_or(Bounds{min:[0.;3],max:[0.;3]});
            meshes.push(Mesh{range:offset..end,bounds,tree,digest:mesh_digest(&triangles[offset..end])});offset=end;
        }
        if offset!=triangles.len(){return Err("S3W1 geometry metadata is incomplete".into());}
        let mut terrain=Vec::new();let mut groups=Vec::new();let mut sources=Vec::new();let mut total=0usize;
        for record in &m.terrain {
            let n=record.n;if !(2..=4096).contains(&n){return Err("Invalid S3W1 terrain dimensions".into());}
            let bytes=read_file(&root,&record.points)?;let visibility=read_file(&root,&record.visibility)?;
            if bytes.len()!=n*n*12||visibility.len()!=n*n{return Err("S3W1 terrain file dimensions mismatch".into());}
            let points:Vec<[f32;3]>=bytes.chunks_exact(12).map(|p|std::array::from_fn(|i|f32::from_le_bytes(p[i*4..i*4+4].try_into().unwrap()))).collect();drop(bytes);
            if !points.iter().flatten().all(|v|v.is_finite()){return Err("Non-finite S3W1 terrain".into());}
            let mut visible=Vec::new();for y in 0..n-1{for x in 0..n-1{let i=y*n+x;
                if [visibility[i],visibility[i+1],visibility[i+n],visibility[i+n+1]].iter().all(|&v|v<170){visible.push((y*(n-1)+x) as u32);}}}
            let count=visible.len()*2;if record.triangle_count!=count||record.triangle_start!=total{return Err("S3W1 terrain visibility/count mismatch".into());}
            let bounds=Bounds::points(points.iter().map(|p|p.map(f64::from)));let given=Bounds{min:record.min,max:record.max};
            if !given.valid() || (0..3).any(|i|bounds.min[i]<given.min[i]||bounds.max[i]>given.max[i]){return Err("S3W1 terrain bounds mismatch".into());}
            let mut tiles=Vec::new();for y in (0..n-1).step_by(16){for x in (0..n-1).step_by(16){
                let ymax=(y+16).min(n-1);let xmax=(x+16).min(n-1);
                let bounds=Bounds::points((y..=ymax).flat_map(|row|(x..=xmax).map(move|col|row*n+col)).map(|i|points[i].map(f64::from)));
                tiles.push(TerrainTile{rows:y..ymax,columns:x..xmax,bounds});}}
            let tree=Bvh::new(&tiles.iter().map(|t|t.bounds).collect::<Vec<_>>());
            if count>0{groups.push(WorldGroup{triangle_range:total..total+count,bounds});sources.push(Source::Terrain(terrain.len()));}
            total+=count;terrain.push(Terrain{n,mirrored:record.mirrored,points,visible,tiles,tree});
        }
        if total!=m.terrain_triangle_count{return Err("S3W1 total terrain count mismatch".into());}
        let bytes=read_file(&root,&m.instance_file)?;
        if m.instance_count.checked_mul(152)!=Some(bytes.len()){return Err("S3W1 instance count mismatch".into());}
        let mut instances=Vec::with_capacity(m.instance_count);
        for data in bytes.chunks_exact(152){
            let geometry=u32::from_le_bytes(data[0..4].try_into().unwrap()) as usize;
            let package=u32::from_le_bytes(data[4..8].try_into().unwrap()) as usize;
            if geometry>=meshes.len()||package>=m.packages.len(){return Err("Invalid S3W1 instance reference".into());}
            let r=std::array::from_fn(|row|vector(data,8+row*24));let v=vector(data,80);
            let bounds=Bounds{min:vector(data,104),max:vector(data,128)};
            if !bounds.valid()||!r.iter().flatten().chain(v.iter()).all(|v|v.is_finite()){return Err("Non-finite S3W1 instance".into());}
            let determinant=determinant(r);let count=meshes[geometry].range.len();
            if count>0 {let end=total.checked_add(count).ok_or("S3W1 triangle count overflow")?;
                // Bounds are recomputed from transformed vertices, avoiding a
                // malformed or rounded-down source box hiding real geometry.
                let local=meshes[geometry].bounds;
                let actual=transformed_bounds(r,v,local).union(bounds);
                groups.push(WorldGroup{triangle_range:total..end,bounds:actual});sources.push(Source::Instance(instances.len()));total=end;}
            instances.push(Instance{geometry,package,r,v,mirrored:determinant<0.});
        }drop(bytes);
        if total.checked_sub(m.terrain_triangle_count)!=Some(m.static_triangle_count){return Err("S3W1 total static count mismatch".into());}
        let tree=Bvh::new(&groups.iter().map(|g|g.bounds).collect::<Vec<_>>());
        let counts=WorldCounts{geometry_triangles:triangles.len(),instances:instances.len(),terrain_components:terrain.len(),
            terrain_triangles:m.terrain_triangle_count,static_triangles:m.static_triangle_count,binary_bytes:m.binary_bytes};
        let mut hashes=HashMap::<String,Sha256>::new();
        for instance in &instances{
            let mesh=&meshes[instance.geometry];if mesh.range.is_empty(){continue;}
            hash_instance(hashes.entry(m.packages[instance.package].clone()).or_default(),mesh,instance);
        }
        let package_content=hashes.into_iter().map(|(p,h)|(p,(false,h.finalize().into()))).collect();
        let package_sequence=package_sequence(sources.iter().filter_map(|source|match source{
            Source::Instance(i)=>Some(m.packages[instances[*i].package].as_str()),_=>None}));
        Ok(Self{source_fingerprint:m.source_fingerprint,counts,triangles,meshes,instances,terrain,groups,sources,tree,total,
            scene_fingerprint:None,base:None,packages:m.packages,default_policy:m.default_shape_complexity,changes:Vec::new(),package_content,package_sequence})
    }
    pub fn with_overlay(base:&Arc<Self>,manifest:&Path)->Result<Self,String>{
        let base=base.base.as_ref().unwrap_or(base).clone();
        let root=manifest.parent().ok_or("S3O1 manifest has no directory")?.canonicalize().map_err(|e|e.to_string())?;
        let bytes=fs::read(manifest).map_err(|e|e.to_string())?;if bytes.len()>32*1024*1024{return Err("S3O1 manifest too large".into());}
        let m:OverlayManifest=serde_json::from_slice(&bytes).map_err(|e|e.to_string())?;
        if m.magic!="S3O1"||m.schema!=1||m.base_fingerprint!=base.source_fingerprint||m.geometry_base_count!=base.geometry_count()
            ||m.instance_stride!=152||m.default_shape_complexity!=base.default_policy||!m.completeness.complete
            ||m.completeness.missing_geometry_count!=0||m.completeness.unsupported_meshes!=0||m.completeness.unresolved_empty_meshes!=0{
            return Err("Unsupported/incomplete or mismatched S3O1 overlay".into());}
        if m.instance_packages.len()<base.packages.len()||m.instance_packages[..base.packages.len()]!=base.packages{
            return Err("S3O1 base package table changed".into());}
        let observed:HashSet<_>=m.packages.iter().cloned().collect();
        if observed.iter().any(|p|p.is_empty()){return Err("Invalid S3O1 observed package".into());}
        let bytes=read_file(&root,&m.geometry_file)?;
        if bytes.len()%72!=0{return Err("S3O1 geometry length invalid".into());}
        let triangles:Vec<_>=bytes.chunks_exact(72).map(|b|[vector(b,0),vector(b,24),vector(b,48)]).collect();drop(bytes);
        if !triangles.iter().flatten().flatten().all(|v|v.is_finite()){return Err("Non-finite S3O1 geometry".into());}
        let mut meshes=Vec::new();let mut offset=0usize;
        for record in &m.geometry{
            let end=offset.checked_add(record.count).filter(|&n|n<=triangles.len()).ok_or("S3O1 geometry range invalid")?;
            if record.offset!=offset||record.unsupported!=0{return Err("S3O1 unsupported geometry".into());}
            if record.count==0{let e=record.empty_evidence.as_ref().ok_or("S3O1 unresolved empty geometry")?;
                let policy=if e.trace==Some(0){Some(base.default_policy)}else{e.trace};
                if e.reason!="no_cooked_physics_shapes"&&!(["complex_only","all_simple_shapes_disabled"].contains(&e.reason.as_str())&&matches!(policy,Some(1|2))){
                    return Err("S3O1 empty collision policy unresolved".into());}}
            let bounds:Vec<_>=triangles[offset..end].iter().map(|t|Bounds::points(*t)).collect();let tree=Bvh::new(&bounds);
            meshes.push(Mesh{range:offset..end,bounds:bounds.into_iter().reduce(Bounds::union).unwrap_or(Bounds{min:[0.;3],max:[0.;3]}),tree,
                digest:mesh_digest(&triangles[offset..end])});offset=end;
        }
        if offset!=triangles.len(){return Err("S3O1 geometry metadata incomplete".into());}
        let mut groups=Vec::new();let mut sources=Vec::new();let mut removed=0;let mut changes=Vec::new();
        for (index,group) in base.groups.iter().enumerate(){
            if base.group_package(index).is_some_and(|p|observed.contains(p)){removed+=group.triangle_range.len();changes.push(group.bounds);continue;}
            groups.push(group.clone());sources.push(Source::Base(index));
        }
        let bytes=read_file(&root,&m.instance_file)?;
        if m.instance_count.checked_mul(152)!=Some(bytes.len()){return Err("S3O1 instance count mismatch".into());}
        let mut instances=Vec::new();let mut total=base.total;
        for data in bytes.chunks_exact(152){
            let geometry=u32::from_le_bytes(data[0..4].try_into().unwrap()) as usize;
            let package=u32::from_le_bytes(data[4..8].try_into().unwrap()) as usize;
            if geometry>=base.geometry_count()+meshes.len()||package>=m.instance_packages.len()||!observed.contains(&m.instance_packages[package]){
                return Err("S3O1 undeclared geometry/package reference".into());}
            let r=std::array::from_fn(|row|vector(data,8+row*24));let v=vector(data,80);let bounds=Bounds{min:vector(data,104),max:vector(data,128)};
            if !bounds.valid()||!r.iter().flatten().chain(v.iter()).all(|v|v.is_finite()){return Err("Non-finite S3O1 instance".into());}
            let mesh=if geometry<base.geometry_count(){base.mesh_data(geometry).0}else{&meshes[geometry-base.geometry_count()]};
            let count=mesh.range.len();if count>0{let end=total.checked_add(count).ok_or("S3O1 triangle count overflow")?;
                let bounds=transformed_bounds(r,v,mesh.bounds).union(bounds);changes.push(bounds);
                groups.push(WorldGroup{triangle_range:total..end,bounds});
                sources.push(Source::Instance(instances.len()));total=end;}
            instances.push(Instance{geometry,package,r,v,mirrored:determinant(r)<0.});
        }
        if total-base.total!=m.triangle_count{return Err("S3O1 triangle total mismatch".into());}
        let tree=Bvh::new(&groups.iter().map(|g|g.bounds).collect::<Vec<_>>());
        let counts=WorldCounts{geometry_triangles:base.counts.geometry_triangles+triangles.len(),instances:base.counts.instances+instances.len(),
            terrain_components:base.counts.terrain_components,terrain_triangles:base.counts.terrain_triangles,
            static_triangles:base.counts.static_triangles-removed+m.triangle_count,binary_bytes:base.counts.binary_bytes+m.binary_bytes};
        let mut package_content=base.package_content.clone();
        for package in &observed{package_content.remove(package);}
        let mut hashes=HashMap::<String,Sha256>::new();
        for instance in &instances{
            let mesh=if instance.geometry<base.geometry_count(){base.mesh_data(instance.geometry).0}else{&meshes[instance.geometry-base.geometry_count()]};
            if mesh.range.is_empty(){continue;}
            hash_instance(hashes.entry(m.instance_packages[instance.package].clone()).or_default(),mesh,instance);
        }
        for (p,h) in hashes{package_content.insert(p,(true,h.finalize().into()));}
        let package_sequence=package_sequence(sources.iter().filter_map(|source|match source{
            Source::Instance(i)=>Some(m.instance_packages[instances[*i].package].as_str()),
            Source::Base(i)=>base.group_package(*i),_=>None}));
        Ok(Self{source_fingerprint:base.source_fingerprint.clone(),scene_fingerprint:Some(m.source_fingerprint),default_policy:base.default_policy,
            base:Some(base),packages:m.instance_packages,counts,triangles,meshes,instances,terrain:Vec::new(),groups,sources,tree,total,changes,package_content,package_sequence})
    }
    pub fn changed_bounds(&self)->Vec<Bounds>{self.changes.clone()}
    /// Only static packages whose actual ordered mesh/transform content changed
    /// since the previously accepted view. Raw live ordinals are deliberately
    /// excluded: adding another package can shift them without moving a face.
    /// The base/live provenance bit detects canonical face-owner order changes
    /// when an otherwise identical package first moves into the live overlay.
    pub fn difference_bounds(&self,previous:&Self)->Result<Vec<Bounds>,String>{
        if self.source_fingerprint!=previous.source_fingerprint{return Err("Cannot compare different compact world bases".into());}
        // A package can contain thousands of static objects and a handful of
        // animated attachments. Compare their actual ordered geometry records
        // before falling back to package-level invalidation. Ordinal shifts do
        // not move a face; an insertion/deletion/movement needs only its own
        // old/new bounds, provided all surviving records retain their order.
        let current=self.ordered_static_records();let old=previous.ordered_static_records();
        let current_set:HashSet<_>=current.iter().map(|&(key,_)|key).collect();
        let old_set:HashSet<_>=old.iter().map(|&(key,_)|key).collect();
        let current_common:Vec<_>=current.iter().filter_map(|&(key,_)|old_set.contains(&key).then_some(key)).collect();
        let old_common:Vec<_>=old.iter().filter_map(|&(key,_)|current_set.contains(&key).then_some(key)).collect();
        if current_common==old_common {
            let mut bounds=Vec::new();
            for &(key,index) in &old {if !current_set.contains(&key){bounds.push(previous.groups[index].bounds);}}
            for &(key,index) in &current {if !old_set.contains(&key){bounds.push(self.groups[index].bounds);}}
            return Ok(bounds);
        }
        // Reordering surviving records can change the original earliest face
        // owner. Keep the established conservative package fallback for that
        // case rather than treating an unordered geometry multiset as equal.
        let mut changed=HashSet::new();
        for package in self.package_content.keys().chain(previous.package_content.keys()){
            if self.package_content.get(package)!=previous.package_content.get(package){changed.insert(package.as_str());}
        }
        let current_order=package_sequence(self.package_sequence.iter().map(String::as_str).filter(|p|!changed.contains(p)));
        let previous_order=package_sequence(previous.package_sequence.iter().map(String::as_str).filter(|p|!changed.contains(p)));
        if current_order!=previous_order{
            // Arbitrary row interleaving can change earliest global edge owners
            // even when each package's internal triangles stayed identical.
            changed.extend(self.package_sequence.iter().map(String::as_str));
            changed.extend(previous.package_sequence.iter().map(String::as_str));
        }
        let mut bounds=Vec::new();
        if !changed.is_empty(){for world in [previous,self]{for (index,group) in world.groups.iter().enumerate(){
            if world.group_package(index).is_some_and(|p|changed.contains(p)){bounds.push(group.bounds);}
        }}}
        Ok(bounds)
    }
    fn ordered_static_records(&self)->Vec<(([u8;32],usize),usize)> {
        let mut occurrences=HashMap::<[u8;32],usize>::new();let mut result=Vec::new();
        for index in 0..self.groups.len(){
            let Some((world,instance,live))=self.static_record(index) else{continue;};
            let mut hash=Sha256::new();hash.update(b"S3W1 ordered static instance v1\0");
            let package=&world.packages[instance.package];hash.update((package.len() as u64).to_le_bytes());hash.update(package.as_bytes());
            hash.update([u8::from(live)]);hash_instance(&mut hash,world.mesh_data(instance.geometry).0,instance);
            let digest:[u8;32]=hash.finalize().into();let ordinal=occurrences.entry(digest).or_default();
            result.push(((digest,*ordinal),index));*ordinal+=1;
        }result
    }
    fn static_record(&self,index:usize)->Option<(&Self,&Instance,bool)> {
        match self.sources[index] {
            Source::Terrain(_)=>None,
            Source::Instance(i)=>Some((self,&self.instances[i],self.base.is_some())),
            Source::Base(i)=>self.base.as_ref().unwrap().static_record(i),
        }
    }
    pub fn pristine_base(self:&Arc<Self>)->Arc<Self>{self.base.as_ref().unwrap_or(self).clone()}
    fn geometry_count(&self)->usize{self.base.as_ref().map_or(0,|base|base.geometry_count())+self.meshes.len()}
    fn mesh_data(&self,index:usize)->(&Mesh,&[[[f64;3];3]]){
        if let Some(base)=&self.base{let count=base.geometry_count();if index<count{return base.mesh_data(index);}
            return(&self.meshes[index-count],&self.triangles);}
        (&self.meshes[index],&self.triangles)
    }
    fn group_package(&self,group:usize)->Option<&str>{match self.sources[group]{Source::Terrain(_)=>None,
        Source::Instance(i)=>Some(&self.packages[self.instances[i].package]),Source::Base(i)=>self.base.as_ref().unwrap().group_package(i)}}
    pub fn triangle_count(&self)->usize { self.total }
    pub fn groups(&self)->&[WorldGroup] { &self.groups }
    /// Actual visible faces in the terrain owner's 3x3 quad neighborhood.
    /// Used only as an early exact-ray test; callers still query the world on a miss.
    pub fn terrain_neighbors(&self,id:usize)->Vec<usize>{
        if id>=self.total{return Vec::new();}
        if let Some(base)=&self.base{if id<base.total{return base.terrain_neighbors(id);}}
        let group=self.group_for_triangle(id);
        let Source::Terrain(index)=self.sources[group] else{return Vec::new();};
        let terrain=&self.terrain[index];let count=terrain.visible.len();let start=self.groups[group].triangle_range.start;
        let quad=terrain.visible[(id-start)%count] as usize;let y=quad/(terrain.n-1);let x=quad%(terrain.n-1);
        let mut result=Vec::with_capacity(18);
        for row in y.saturating_sub(1)..=(y+1).min(terrain.n-2){for col in x.saturating_sub(1)..=(x+1).min(terrain.n-2){
            if let Ok(ordinal)=terrain.visible.binary_search(&((row*(terrain.n-1)+col) as u32)){
                result.push(start+ordinal);result.push(start+count+ordinal);
            }
        }}
        result.sort_unstable();result
    }
    fn group_for_triangle(&self,id:usize)->usize {
        assert!(id<self.total,"S3W1 triangle index outside world");self.groups.partition_point(|g|g.triangle_range.end<=id)
    }
    pub fn raw_triangle(&self,id:usize)->[[f64;3];3] {
        if let Some(base)=&self.base{if id<base.total{return base.raw_triangle(id);}}
        let group=self.group_for_triangle(id);let local=id-self.groups[group].triangle_range.start;
        match self.sources[group] {
            Source::Terrain(index)=>{let t=&self.terrain[index];let count=t.visible.len();let quad=t.visible[local%count] as usize;
                let y=quad/(t.n-1);let x=quad%(t.n-1);let a=y*t.n+x;let ids=if local<count{[a,a+1,a+t.n+1]}else{[a,a+t.n+1,a+t.n]};
                let mut triangle=ids.map(|i|t.points[i].map(f64::from));if t.mirrored{triangle.swap(1,2);}triangle},
            Source::Instance(index)=>{let instance=&self.instances[index];let(mesh,triangles)=self.mesh_data(instance.geometry);
                let mut triangle=triangles[mesh.range.start+local].map(|p|transform(instance.r,instance.v,p));
                if instance.mirrored{triangle.swap(1,2);}triangle},
            Source::Base(_)=>self.base.as_ref().unwrap().raw_triangle(id),
        }
    }
    pub fn candidate_groups(&self,min:[f64;3],max:[f64;3])->Vec<usize> {
        let query=Bounds{min,max};if !query.valid(){return Vec::new();}
        let mut result=self.tree.query(query);result.retain(|&i|self.groups[i].bounds.overlaps(query));result.sort_unstable();result
    }
    pub fn candidate_triangles(&self,min:[f64;3],max:[f64;3])->Vec<usize> {
        let query=Bounds{min,max};let mut out=Vec::new();
        for group in self.candidate_groups(min,max){self.candidates_for_group(group,query,&mut out);}
        out.sort_unstable();out.dedup();out
    }
    /// Exact sorted union of individual queries, visiting each active group
    /// only once. Invalid boxes contribute no candidates, like the scalar API.
    /// Terrain visibility, mirrored instances and masked base groups retain
    /// their original IDs and original world-space triangle-AABB predicate.
    pub fn candidate_triangles_many(&self,queries:&[Bounds])->Vec<usize> {
        let queries=BoundsIndex::new(queries);
        if queries.boxes.is_empty(){return Vec::new();}
        let mut out=Vec::new();
        for (index,group) in self.groups.iter().enumerate() {
            // Outward padding accounts for rounding between transformed box
            // corners and separately transformed vertices before range insertion.
            let pad=group.bounds.min.iter().chain(group.bounds.max.iter()).fold(1f64,|a,v|a.max(v.abs()))*f64::EPSILON*64.;
            let proof=Bounds{min:group.bounds.min.map(|v|v-pad),max:group.bounds.max.map(|v|v+pad)};
            let (full,union)=queries.tree.coverage(group.bounds,proof,&queries.boxes);
            if full {out.extend(group.triangle_range.clone());continue;}
            let Some(union)=union else {continue};
            let start=out.len();self.candidates_for_group(index,union,&mut out);
            let mut end=start;
            for at in start..out.len() {
                let id=out[at];
                if queries.tree.any_overlap(Bounds::points(self.raw_triangle(id)),&queries.boxes){out[end]=id;end+=1;}
            }
            out.truncate(end);out[start..].sort_unstable();
        }
        // Active groups are ordered disjoint ranges, and each local BVH/terrain
        // tile visits a triangle once. Sorting each partial range therefore
        // preserves global canonical order without a large final sort/set.
        out
    }
    fn candidates_for_group(&self,group:usize,query:Bounds,out:&mut Vec<usize>){
        let start=self.groups[group].triangle_range.start;
            match self.sources[group] {
                Source::Terrain(index)=>{let t=&self.terrain[index];for tile in t.tree.query(query){let tile=&t.tiles[tile];if !tile.bounds.overlaps(query){continue;}
                    for row in tile.rows.clone(){let low=(row*(t.n-1)+tile.columns.start) as u32;let high=(row*(t.n-1)+tile.columns.end) as u32;
                        let a=t.visible.partition_point(|&v|v<low);let b=t.visible.partition_point(|&v|v<high);
                        for ordinal in a..b{for local in [ordinal,ordinal+t.visible.len()]{let id=start+local;
                            if Bounds::points(self.raw_triangle(id)).overlaps(query){out.push(id);}}}}}},
                Source::Instance(index)=>{let instance=&self.instances[index];let(mesh,_)=self.mesh_data(instance.geometry);
                    // Transform visited BVH boxes, retaining exact world-AABB
                    // candidates even for rotated or singular instances.
                    let candidates=mesh.tree.query_transformed(query,instance.r,instance.v);
                    for local in candidates{let id=start+local;if Bounds::points(self.raw_triangle(id)).overlaps(query){out.push(id);}}},
                Source::Base(index)=>self.base.as_ref().unwrap().candidates_for_group(index,query,out),
            }
    }
}
