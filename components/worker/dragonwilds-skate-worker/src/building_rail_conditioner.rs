//! Conservative, scene-only rail authoring for verified Castle double frames.
//! Physical triangles, original lip/merge caches and Skate simulation are never
//! changed. Existing straight rails are raised in place only over proven support.
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::{collections::HashSet,fs,path::{Component,Path}};

pub type Triangle=[[f64;3];3];
pub type Rails=Vec<Vec<[f32;3]>>;
pub const TARGET:&str="/Game/Art/Env/Base_Building/BuildingKit/Tier3/SM_BB_T3_Wall_Doorframe_Double.SM_BB_T3_Wall_Doorframe_Double";
const MAX_RISE:f64=3.2;
#[derive(Clone,Copy,Debug,PartialEq)]
struct Box3 {lo:[f64;3],hi:[f64;3]}
#[derive(Clone,Debug,PartialEq)]
struct Model {boxes:[Box3;3],bounds:Box3,top:f64,lower:f64}
#[derive(Clone)]
struct Frame {model:Model,translation:[f64;3],bounds:Box3,identity:bool}
#[derive(Default,Debug,Serialize)]
pub struct Report {
 pub target_instances:usize,pub eligible_instances:usize,pub considered_rails:usize,
 pub raised_rails:usize,pub rejected_layout:usize,pub rejected_support:usize,
 pub rejected_obstacles:usize,pub maximum_rise_cm:f64,
}
#[derive(Deserialize)]struct File {path:String,bytes:usize,sha256:String}
#[derive(Deserialize)]struct Geometry {path:String,offset:usize,count:usize,unsupported:usize}
#[derive(Deserialize)]struct Complete {complete:bool}
#[derive(Deserialize)]struct Manifest {
 magic:String,schema:u32,source_fingerprint:String,geometry_base_count:usize,
 geometry:Vec<Geometry>,geometry_file:File,instance_file:File,instance_count:usize,
 instance_stride:usize,instance_packages:Vec<String>,packages:Vec<String>,completeness:Complete,
}
fn read(root:&Path,file:&File)->Result<Vec<u8>,String>{
 if file.path.is_empty()||Path::new(&file.path).components().any(|p|!matches!(p,Component::Normal(_))){return Err("Invalid conditioning input path".into());}
 let path=root.join(&file.path);let canonical=path.canonicalize().map_err(|e|e.to_string())?;
 if !canonical.starts_with(root){return Err("Conditioning input escapes scene".into());}
 let bytes=fs::read(canonical).map_err(|e|e.to_string())?;
 if bytes.len()!=file.bytes||Sha256::digest(&bytes).iter().map(|b|format!("{b:02x}")).collect::<String>()!=file.sha256.to_lowercase(){return Err("Conditioning input hash/size mismatch".into());}
 Ok(bytes)
}
fn number(bytes:&[u8],at:usize)->f64 {f64::from_le_bytes(bytes[at..at+8].try_into().unwrap())}
fn vector(bytes:&[u8],at:usize)->[f64;3] {std::array::from_fn(|i|number(bytes,at+i*8))}
fn bounds(tris:&[Triangle])->Box3 {Box3{
 lo:std::array::from_fn(|a|tris.iter().flatten().map(|p|p[a]).fold(f64::INFINITY,f64::min)),
 hi:std::array::from_fn(|a|tris.iter().flatten().map(|p|p[a]).fold(f64::NEG_INFINITY,f64::max))}}
fn sub(a:[f64;3],b:[f64;3])->[f64;3]{std::array::from_fn(|i|a[i]-b[i])}
fn dot(a:[f64;3],b:[f64;3])->f64{(0..3).map(|i|a[i]*b[i]).sum()}
fn cross(a:[f64;3],b:[f64;3])->[f64;3]{[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}
fn closed_box(tris:&[Triangle])->Option<Box3>{
 if tris.len()!=12||!tris.iter().flatten().flatten().all(|v|v.is_finite()){return None;}
 let b=bounds(tris);if (0..3).any(|a|b.hi[a]<=b.lo[a]){return None;}
 let mut faces:Vec<Vec<[u8;3]>>=vec![vec![];6];
 for t in tris {
  let mut corners=[0u8;3];
  for i in 0..3 {for a in 0..3 {
   if t[i][a]==b.hi[a]{corners[i]|=1<<a;}else if t[i][a]!=b.lo[a]{return None;}
  }}
  let a=(0..3).find(|&a|t[0][a]==t[1][a]&&t[1][a]==t[2][a])?;
  let high=t[0][a]==b.hi[a];let normal=cross(sub(t[1],t[0]),sub(t[2],t[0]));
  let area=(0..3).filter(|&v|v!=a).map(|v|b.hi[v]-b.lo[v]).product::<f64>();
  if (normal[a]-(if high{area}else{-area})).abs()>area*1e-12{return None;}
  faces[a*2+usize::from(high)].push(corners);
 }
 for (face,triangles) in faces.iter().enumerate(){
  if triangles.len()!=2{return None;}
  let all:HashSet<_>=triangles.iter().flatten().copied().collect();
  let common:Vec<_>=triangles[0].iter().copied().filter(|v|triangles[1].contains(v)).collect();
  if all.len()!=4||common.len()!=2||(common[0]^common[1])!=(7^(1<<(face/2))){return None;}
 }
 Some(b)
}
fn model(tris:&[Triangle])->Option<Model>{
 if tris.len()!=36{return None;}
 let mut boxes=[closed_box(&tris[..12])?,closed_box(&tris[12..24])?,closed_box(&tris[24..])?];
 boxes.sort_by(|a,b|a.lo[2].total_cmp(&b.lo[2]).then(a.lo[0].total_cmp(&b.lo[0])));
 let [left,right,beam]=boxes;
 if left.lo[2]!=right.lo[2]||left.hi[2]!=right.hi[2]||beam.lo[2]<=left.lo[2]
  ||beam.hi[2]>=left.hi[2]||left.hi[2]-beam.hi[2]>MAX_RISE
  ||boxes.iter().any(|b|b.lo[1]!=left.lo[1]||b.hi[1]!=left.hi[1])
  ||left.hi[1]-left.lo[1]<=18.||beam.hi[0]-beam.lo[0]<60.96
  ||left.lo[0]>=beam.lo[0]||left.hi[0]<beam.lo[0]
  ||right.hi[0]<=beam.hi[0]||right.lo[0]>beam.hi[0]
  ||left.hi[0]>=right.lo[0]{return None;}
 Some(Model{boxes,bounds:bounds(tris),top:left.hi[2],lower:beam.hi[2]})
}
fn load(scene:&Path,expected:&str)->Result<Vec<Frame>,String>{
 let root=scene.parent().ok_or("Scene directory missing")?.canonicalize().map_err(|e|e.to_string())?;
 let raw=fs::read(scene).map_err(|e|e.to_string())?;if raw.len()>32*1024*1024{return Err("Conditioning manifest too large".into());}
 let m:Manifest=serde_json::from_slice(&raw).map_err(|e|e.to_string())?;
 if m.magic!="S3O1"||m.schema!=1||m.source_fingerprint!=expected||!m.completeness.complete||m.instance_stride!=152{
  return Err("Conditioning requires the validated complete scene identity".into());}
 let selected:Vec<_>=m.geometry.iter().enumerate().filter(|(_,g)|g.path==TARGET&&g.unsupported==0).collect();
 if selected.is_empty(){return Ok(vec![]);}
 let geometry=read(&root,&m.geometry_file)?;let bytes=read(&root,&m.instance_file)?;
 if m.instance_count.checked_mul(152)!=Some(bytes.len())||geometry.len()%72!=0{return Err("Invalid conditioning scene layout".into());}
 let mut models=Vec::new();
 for (i,g) in selected {
  let start=g.offset.checked_mul(72).ok_or("Geometry offset overflow")?;
  let end=g.offset.checked_add(g.count).and_then(|n|n.checked_mul(72)).filter(|&n|n<=geometry.len()).ok_or("Geometry range overflow")?;
  let tris:Vec<Triangle>=geometry[start..end].chunks_exact(72).map(|t|std::array::from_fn(|p|vector(t,p*24))).collect();
  if let Some(shape)=model(&tris){models.push((m.geometry_base_count+i,shape));}
 }
 let mut frames=Vec::new();
 for row in bytes.chunks_exact(152){
  let index=u32::from_le_bytes(row[..4].try_into().unwrap())as usize;
  let Some((_,model))=models.iter().find(|(i,_)|*i==index)else{continue};
  let package=u32::from_le_bytes(row[4..8].try_into().unwrap())as usize;
  let Some(package)=m.instance_packages.get(package)else{return Err("Invalid conditioning package index".into());};
  if !package.starts_with("/SkateRuntime/Buildings/")||!m.packages.contains(package){continue;}
  let r:[[f64;3];3]=std::array::from_fn(|i|vector(row,8+i*24));let v=vector(row,80);
  let b=Box3{lo:vector(row,104),hi:vector(row,128)};
  if !r.iter().flatten().chain(v.iter()).chain(b.lo.iter()).chain(b.hi.iter()).all(|v|v.is_finite())||(0..3).any(|a|b.lo[a]>b.hi[a]){return Err("Non-finite conditioning transform".into());}
  let identity=(0..3).all(|i|(0..3).all(|j|r[i][j]==if i==j{1.}else{0.}));
  frames.push(Frame{model:model.clone(),translation:v,bounds:b,identity});
 }
 Ok(frames)
}
fn overlap(a:Box3,b:Box3)->bool{(0..3).all(|i|a.lo[i]<=b.hi[i]&&b.lo[i]<=a.hi[i])}
// Exact triangle/AABB SAT. The raised probe's box encloses its tiny capsule;
// rejecting extra corner contacts is conservative, never a missed obstruction.
fn intersects(tri:Triangle,b:Box3)->bool{
 if !overlap(bounds(&[tri]),b){return false;}
 let center=std::array::from_fn(|i|(b.lo[i]+b.hi[i])*0.5);
 let half:[f64;3]=std::array::from_fn(|i|(b.hi[i]-b.lo[i])*0.5);
 let t=tri.map(|p|sub(p,center));let axes=[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]];
 let edges=[sub(t[1],t[0]),sub(t[2],t[1]),sub(t[0],t[2])];
 let separated=|axis:[f64;3]|{let p=t.map(|v|dot(v,axis));let radius=(0..3).map(|i|half[i]*axis[i].abs()).sum::<f64>();
  p.into_iter().fold(f64::INFINITY,f64::min)>radius||p.into_iter().fold(f64::NEG_INFINITY,f64::max)< -radius};
 if separated(cross(edges[0],edges[1])){return false;}
 for edge in edges{for axis in axes{if separated(cross(edge,axis)){return false;}}}
 true
}
fn covered(mut spans:Vec<(f64,f64)>,start:f64,end:f64)->bool{
 spans.sort_by(|a,b|a.0.total_cmp(&b.0));let mut to=start;
 for (lo,hi)in spans{if hi<to{continue;}if lo>to{return false;}to=to.max(hi);if to>=end{return true;}}
 false
}
fn unreal(p:[f32;3])->[f64;3]{[f64::from(p[0])*100.,-f64::from(p[2])*100.,f64::from(p[1])*100.]}

/// Call only after the original raw rails have been loaded/merged. `query`
/// returns real physical triangles from the same already validated scene view.
/// No caller may run stock merge/chain again on the returned rails.
pub fn condition(scene:&Path,expected_fingerprint:&str,mut rails:Rails,
 mut query:impl FnMut([f64;3],[f64;3])->Vec<Triangle>)->Result<(Rails,Report),String>{
 let frames=load(scene,expected_fingerprint)?;
 let mut report=Report{target_instances:frames.len(),eligible_instances:frames.iter().filter(|f|f.identity).count(),..Default::default()};
 if frames.is_empty(){return Ok((rails,report));}
 for rail in &mut rails{
  if rail.len()<2||!rail.iter().flatten().all(|v|v.is_finite()){continue;}
  let a=unreal(rail[0]);let z=unreal(*rail.last().unwrap());let (start,end)=(a[0].min(z[0]),a[0].max(z[0]));
  if end-start<60.96||rail.iter().any(|p|p[1]!=rail[0][1]||p[2]!=rail[0][2])
   ||rail.windows(2).any(|p|(p[1][0]-p[0][0])*(rail.last().unwrap()[0]-rail[0][0])<=0.){continue;}
  // Only account for actual f32 world-coordinate conversion, not centimetres
  // of geometric smoothing, in the identity matching of an outer edge.
  let epsilon=a.into_iter().chain(z).map(f64::abs).fold(1.,f64::max)*f64::from(f32::EPSILON)*4.+1e-6;
  let mut choice=None;
  for f in &frames{if !f.identity{continue;}
   if f.model.bounds.hi[0]+f.translation[0]<start||f.model.bounds.lo[0]+f.translation[0]>end{continue;}
   for side in [0usize,1]{let y=if side==0{f.model.bounds.lo[1]}else{f.model.bounds.hi[1]}+f.translation[1];
    if (y-a[1]).abs()<=epsilon&&(f.model.lower+f.translation[2]-a[2]).abs()<=epsilon{choice=Some((f,side));break;}}
   if choice.is_some(){break;}
  }
  let Some((first,side))=choice else{continue;};report.considered_rails+=1;
  let top=first.model.top+first.translation[2];let rise=top-a[2];
  if rise<=epsilon||rise>MAX_RISE {report.rejected_layout+=1;continue;}
  let inward=if side==0{1.}else{-1.};let inside=a[1]+inward*9.;let outside=a[1]-inward*9.;
  let mut spans=Vec::new();let mut bad_layout=false;
  for f in &frames{
   if f.bounds.hi[0]<start||f.bounds.lo[0]>end||f.bounds.hi[1]<a[1]-9.||f.bounds.lo[1]>a[1]+9.{continue;}
   if !f.identity||f.model!=first.model||f.translation[1]!=first.translation[1]||f.translation[2]!=first.translation[2]{bad_layout=true;break;}
   for b in f.model.boxes{
    let height=b.hi[2]+f.translation[2];
    if inside>=b.lo[1]+f.translation[1]&&inside<=b.hi[1]+f.translation[1]&&height<=top&&top-height<=MAX_RISE{
     spans.push((b.lo[0]+f.translation[0],b.hi[0]+f.translation[0]));
    }
   }
  }
  if bad_layout{report.rejected_layout+=1;continue;}
  if !covered(spans,start,end){report.rejected_support+=1;continue;}
  // The installed rail and original probe offsets are f32. Enclose their
  // rounding in every axis, including line ends and the converted top height.
  let padded=|b:Box3|Box3{lo:b.lo.map(|v|v-epsilon),hi:b.hi.map(|v|v+epsilon)};
  let outside_probe=padded(Box3{lo:[start,outside-1e-6,top-4.],hi:[end,outside+1e-6,top+4.]});
  let raised=padded(Box3{lo:[start-0.1,a[1]-9.1,top+8.9],hi:[end+0.1,a[1]+9.1,top+9.1]});
  if [outside_probe,raised].into_iter().any(|b|query(b.lo,b.hi).into_iter().any(|t|intersects(t,b))){report.rejected_obstacles+=1;continue;}
  let height=(top*0.01)as f32;
  for point in rail{point[1]=height;}
  report.raised_rails+=1;report.maximum_rise_cm=report.maximum_rise_cm.max(rise);
 }
 Ok((rails,report))
}
